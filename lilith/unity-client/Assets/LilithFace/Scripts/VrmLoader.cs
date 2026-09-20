// LILITH-CORE · Unity-клиент лица (этап 6)
// Загрузчик VRM: горячий своп personas/<id>/model.vrm по кадру "persona".
//
// Решение D5.4: тело живёт ВНЕ репозитория, поэтому грузим либо с локального
// диска (путь из face.yaml), либо по HTTP с сервера (/api/face/personas/<id>/model.vrm).
// API UniVRM 0.131.x: Vrm10.LoadPathAsync / Vrm10.LoadBytesAsync → Vrm10Instance.

using System;
using System.Collections;
using System.Collections.Generic;
using System.IO;
using UnityEngine;
using UnityEngine.Networking;

#if LILITH_UNIVRM
using UniVRM10;
#endif

namespace Lilith.Face
{
    /// <summary>Результат загрузки тела.</summary>
    public enum VrmLoadResult
    {
        Ok,
        NotFound,
        DownloadFailed,
        LoadFailed,
        UniVrmMissing,
    }

    /// <summary>
    /// Загружает и подменяет VRM-тело персоны на лету.
    /// MonoBehaviour: корутины загрузки живут на сцене.
    /// </summary>
    public class VrmLoader : MonoBehaviour
    {
        [Header("Куда ставим тело")]
        [Tooltip("Родительский трансформ для загруженной модели.")]
        public Transform root;

        [Tooltip("Локальная позиция модели внутри root.")]
        public Vector3 localPosition = new Vector3(0f, 0f, 0f);

        [Tooltip("Локальный поворот модели (VRM смотрит в +Z).")]
        public Vector3 localEulerAngles = new Vector3(0f, 180f, 0f);

        [Tooltip("Масштаб модели.")]
        public Vector3 localScale = new Vector3(1f, 1f, 1f);

        [Header("Сеть")]
        [Tooltip("База HTTP сервера (для скачивания model.vrm по /api/face/personas/<id>/model.vrm).")]
        public string serverBaseUrl = "http://127.0.0.1:8765";

        [Tooltip("Таймаут скачивания, сек.")]
        public int downloadTimeoutSec = 60;

        /// <summary>Текущая загруженная модель (или null).</summary>
        public GameObject Model { get; private set; }

        /// <summary>Id персоны, чьё тело сейчас на сцене.</summary>
        public string CurrentPersona { get; private set; } = "";

        /// <summary>Последняя ошибка загрузки — для оверлея и логов.</summary>
        public string LastError { get; private set; } = "";

        /// <summary>Идёт ли загрузка прямо сейчас.</summary>
        public bool Loading { get; private set; }

        /// <summary>Вызывается после успешной загрузки: (персона, модель).</summary>
        public event Action<string, GameObject> Loaded;

        /// <summary>Вызывается при ошибке: (персона, причина).</summary>
        public event Action<string, VrmLoadResult> Failed;

        private Coroutine _running;
        private int _generation;

        /// <summary>
        /// Горячий своп тела персоны.
        /// </summary>
        /// <param name="personaId">Id персоны.</param>
        /// <param name="localPath">Локальный путь к .vrm (из face.yaml); может быть пустым.</param>
        /// <param name="serverRelativePath">Относительный URL на сервере (из кадра persona.vrm).</param>
        public void Swap(string personaId, string localPath, string serverRelativePath = "")
        {
            if (Loading)
            {
                if (_running != null)
                {
                    StopCoroutine(_running);
                }
            }

            _generation++;
            _running = StartCoroutine(SwapRoutine(personaId, localPath, serverRelativePath, _generation));
        }

        /// <summary>Убрать тело со сцены (например, при отключении от сервера).</summary>
        public void Clear()
        {
            if (_running != null)
            {
                StopCoroutine(_running);
                _running = null;
            }

            DestroyModel();
            CurrentPersona = "";
        }

        private IEnumerator SwapRoutine(string personaId, string localPath, string serverRelativePath, int generation)
        {
            Loading = true;
            LastError = "";

            byte[] bytes = null;
            var path = ResolveLocalPath(localPath);
            if (!string.IsNullOrEmpty(path) && File.Exists(path))
            {
                bytes = File.ReadAllBytes(path);
            }
            else if (!string.IsNullOrEmpty(serverRelativePath))
            {
                var url = BuildUrl(serverRelativePath);
                using (var request = UnityWebRequest.Get(url))
                {
                    request.timeout = downloadTimeoutSec;
                    yield return request.SendWebRequest();
#if UNITY_2020_1_OR_NEWER
                    if (request.result != UnityWebRequest.Result.Success)
#else
                    if (request.isNetworkError || request.isHttpError)
#endif
                    {
                        Loading = false;
                        LastError = $"{url}: {request.error}";
                        Debug.LogWarning($"[Lilith] не скачать VRM: {LastError}");
                        Failed?.Invoke(personaId, VrmLoadResult.DownloadFailed);
                        yield break;
                    }

                    bytes = request.downloadHandler.data;
                }
            }
            else
            {
                Loading = false;
                LastError = "нет ни локального пути, ни URL модели";
                Debug.LogWarning($"[Lilith] {personaId}: {LastError}. " +
                                 "Положи .vrm по пути из face.yaml или отдай его через /api/face/personas/<id>/model.vrm");
                Failed?.Invoke(personaId, VrmLoadResult.NotFound);
                yield break;
            }

            if (bytes == null || bytes.Length < 4)
            {
                Loading = false;
                LastError = "пустой файл модели";
                Failed?.Invoke(personaId, VrmLoadResult.NotFound);
                yield break;
            }

            if (generation != _generation)
            {
                // Пока качали, пришёл новый своп — этот уже не нужен.
                Loading = false;
                yield break;
            }

#if LILITH_UNIVRM
            Vrm10Instance instance = null;
            try
            {
                instance = await Vrm10.LoadBytesAsync(
                    bytes,
                    canLoadVrm0X: true,
                    controlRigGenerationOption: ControlRigGenerationOption.Generate,
                    showMeshes: true,
                    awaitCaller: new RuntimeOnlyAwaitCaller());
            }
            catch (Exception ex)
            {
                Loading = false;
                LastError = ex.Message;
                Debug.LogError($"[Lilith] VRM не загрузился: {ex}");
                Failed?.Invoke(personaId, VrmLoadResult.LoadFailed);
                yield break;
            }

            if (instance == null)
            {
                Loading = false;
                LastError = "Vrm10.LoadBytesAsync вернул null";
                Failed?.Invoke(personaId, VrmLoadResult.LoadFailed);
                yield break;
            }

            DestroyModel();
            Model = instance.gameObject;
            Model.name = $"persona_{personaId}";
            if (root != null)
            {
                Model.transform.SetParent(root, false);
            }

            Model.transform.localPosition = localPosition;
            Model.transform.localEulerAngles = localEulerAngles;
            Model.transform.localScale = localScale;

            CurrentPersona = personaId;
            Loading = false;
            Loaded?.Invoke(personaId, Model);
#else
            // Без UniVRM загрузка невозможна, но и «падать» клиент не должен:
            // сообщаем понятно, чтобы Кирюша знал, что делать.
            DestroyModel();
            Loading = false;
            LastError = "LILITH_UNIVRM не определён: установи UniVRM и добавь символ в Scripting Define Symbols";
            Debug.LogError("[Lilith] " + LastError);
            Failed?.Invoke(personaId, VrmLoadResult.UniVrmMissing);
            yield break;
#endif
        }

        /// <summary>Превратить относительный путь сервера в полный URL.</summary>
        public string BuildUrl(string relativePath)
        {
            if (string.IsNullOrEmpty(relativePath))
            {
                return serverBaseUrl;
            }

            if (relativePath.StartsWith("http://", StringComparison.OrdinalIgnoreCase) ||
                relativePath.StartsWith("https://", StringComparison.OrdinalIgnoreCase))
            {
                return relativePath;
            }

            var baseUrl = (serverBaseUrl ?? "").TrimEnd('/');
            var path = relativePath.StartsWith("/") ? relativePath : "/" + relativePath;
            return baseUrl + path;
        }

        /// <summary>
        /// Разрешить локальный путь: поддерживаем <c>~</c>, переменные окружения
        /// и относительные пути от <see cref="Application.persistentDataPath"/>.
        /// </summary>
        public static string ResolveLocalPath(string rawPath)
        {
            if (string.IsNullOrEmpty(rawPath))
            {
                return "";
            }

            var path = rawPath.Trim();
            if (path.StartsWith("~", StringComparison.Ordinal))
            {
                var home = Environment.GetEnvironmentVariable("USERPROFILE");
                if (string.IsNullOrEmpty(home))
                {
                    home = Environment.GetEnvironmentVariable("HOME");
                }

                path = string.IsNullOrEmpty(home) ? path.TrimStart('~') : Path.Combine(home, path.TrimStart('~', '/', '\\'));
            }

            path = Environment.ExpandEnvironmentVariables(path);
            if (Path.IsPathRooted(path))
            {
                return path;
            }

            var fromPersistent = Path.Combine(Application.persistentDataPath, path);
            return File.Exists(fromPersistent) ? fromPersistent : Path.Combine(Application.dataPath, path);
        }

        private void DestroyModel()
        {
            if (Model != null)
            {
                Destroy(Model);
                Model = null;
            }
        }

        private void OnDestroy()
        {
            DestroyModel();
        }

        /// <summary>Список доступных тел в каталоге персон (для отладочной панели).</summary>
        public static List<string> ScanLocalPersonas(string personasRoot)
        {
            var result = new List<string>();
            if (string.IsNullOrEmpty(personasRoot) || !Directory.Exists(personasRoot))
            {
                return result;
            }

            foreach (var dir in Directory.GetDirectories(personasRoot))
            {
                if (File.Exists(Path.Combine(dir, "model.vrm")))
                {
                    result.Add(Path.GetFileName(dir));
                }
            }

            return result;
        }
    }
}
