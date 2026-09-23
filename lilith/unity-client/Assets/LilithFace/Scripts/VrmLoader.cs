// LILITH-CORE · Unity-клиент лица (этап 6)
// Загрузчик VRM: горячий своп personas/<id>/model.vrm по кадру "persona".
//
// Хотфикс 0.6.4 (блокер F7 «тело не доезжает, Console чистая»): URL тела клиент
// строит САМ — ModelUrlFor()/BuildModelUrl(); своп идемпотентен (гвард force);
// старт/финиш/отказ логируются безусловно, а не под config.verbose.
//
// Хотфикс 0.6.5 (в переписке — 0.6.4.1): ДЕДЛОК РЕДАКТОРА. `yield return loadTask`
// НЕ ждёт завершения Task (Unity 6000.0 из корутины умеет ждать только Awaitable,
// а Task/generic Awaitable<T> — нет), поэтому код шёл дальше и брал
// `loadTask.Result` у НЕзавершённой задачи. `.Result` блокирует главный поток,
// а продолжения UniVRM планируются тем же player loop'ом (NextFrameTaskScheduler
// → UnityLoopTaskScheduler.Update) → задача не может завершиться никогда.
// Лечение: ждём по кадру (`while (!loadTask.IsCompleted) yield return null;`),
// `.Result` — только после IsCompleted, плюс таймаут и фазовые логи.
//
// Решение D5.4: тело живёт ВНЕ репозитория, поэтому грузим либо с локального
// диска (путь из face.yaml), либо по HTTP с сервера (/api/face/personas/<id>/model.vrm).
// API UniVRM 0.131.2 (сверено по исходникам vrm-c/UniVRM, master):
//   UniVRM10.Vrm10.LoadBytesAsync(byte[], bool canLoadVrm0X, ControlRigGenerationOption,
//                                 bool showMeshes, IAwaitCaller awaitCaller, …) → Task<Vrm10Instance>
//   UniGLTF.RuntimeOnlyAwaitCaller(float timeOutInSeconds = 1f/1000f) : UniGLTF.IAwaitCaller
//       — сборка UniGLTF.Utils, только Play Mode (иначе NotSupportedException).
//   UniGLTF.ImmediateCaller — для Edit Mode (не используется: загрузка у нас в рантайме).

using System;
using System.Collections;
using System.Collections.Generic;
using System.IO;
using UnityEngine;
using UnityEngine.Networking;

#if LILITH_UNIVRM
// Хотфикс 0.6.3 (CS0246): IAwaitCaller и RuntimeOnlyAwaitCaller объявлены в
// неймспейсе UniGLTF, а физически лежат в сборке UniGLTF.Utils
// (Packages/UniGLTF/Runtime/Utils/AwaitCaller/*.cs). Без этого using тип не находится,
// хотя ссылка на сборку в .asmdef проставлена.
using UniGLTF;
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
    /// Сводка успешной загрузки тела (хотфикс 0.6.4): что приехало, откуда и куда встало.
    /// Нужна, чтобы «тело доехало» доказывалось логом и оверлеем, а не поиском в Hierarchy.
    /// </summary>
    public readonly struct BodyInfo
    {
        /// <summary>Чья модель.</summary>
        public readonly string PersonaId;

        /// <summary>Сколько байт приехало.</summary>
        public readonly int Bytes;

        /// <summary>Сколько секунд занял своп (скачивание + LoadBytesAsync).</summary>
        public readonly float ElapsedSeconds;

        /// <summary>Сколько трансформов в готовом теле (0 = тело пустое).</summary>
        public readonly int Transforms;

        /// <summary>Имя родителя, в который встало тело.</summary>
        public readonly string Parent;

        /// <summary>Собрать сводку.</summary>
        public BodyInfo(string personaId, int bytes, float elapsedSeconds, int transforms, string parent)
        {
            PersonaId = personaId ?? "";
            Bytes = bytes;
            ElapsedSeconds = elapsedSeconds;
            Transforms = transforms;
            Parent = parent ?? "";
        }

        /// <summary>Есть ли что показывать (пустая сводка = тело ещё не грузилось).</summary>
        public bool IsValid
        {
            get { return !string.IsNullOrEmpty(PersonaId) && Bytes > 0; }
        }

        /// <summary>Одна строка для оверлея.</summary>
        public string Describe()
        {
            if (!IsValid)
            {
                return "нет";
            }

            return $"{PersonaId} · {Bytes / 1048576f:F2} МБ · {ElapsedSeconds * 1000f:F0} мс · " +
                   $"трансформов {Transforms} · родитель {Parent}";
        }
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

        [Tooltip("Параметр UniGLTF.RuntimeOnlyAwaitCaller: через сколько секунд\n" +
                 "загрузчик считает, что пора отдать кадр Unity (NextFrameIfTimedOut).\n" +
                 "Дефолт UniVRM — 1 мс; больше = плавнее загрузка, меньше просадка fps.")]
        public float awaitTimeoutSeconds = 0.001f;

        [Tooltip("Хотфикс 0.6.5: сколько секунд ждать завершения Vrm10.LoadBytesAsync.\n" +
                 "Ждём НЕ блокируя главный поток (по кадру), поэтому зависание\n" +
                 "превращается в понятный ОТКАЗ в Console. 0 = ждать вечно.")]
        public float loadTimeoutSeconds = 60f;

        [Tooltip("Хотфикс 0.6.5: грузить тело ТОЛЬКО по HTTP с сервера, игнорируя\n" +
                 "face.yaml:vrm_path из кадра persona. Диагностика: позволяет сравнить\n" +
                 "ветку файла и ветку сервера на одних и тех же байтах.")]
        public bool loadFromServerOnly = false;

        [Tooltip("Хотфикс 0.6.5: использовать UniGLTF.ImmediateCaller (вся загрузка\n" +
                 "в одном кадре, без next-frame планировщика) вместо RuntimeOnlyAwaitCaller.\n" +
                 "Дедлок исключён конструктивно; цена — один длинный кадр.\n" +
                 "Для больших тел (20+ МБ) держать выключенным.")]
        public bool useImmediateAwaitCaller = false;

        /// <summary>Текущая загруженная модель (или null).</summary>
        public GameObject Model { get; private set; }

        /// <summary>Id персоны, чьё тело сейчас на сцене.</summary>
        public string CurrentPersona { get; private set; } = "";

        /// <summary>Последняя ошибка загрузки — для оверлея и логов.</summary>
        public string LastError { get; private set; } = "";

        /// <summary>Идёт ли загрузка прямо сейчас.</summary>
        public bool Loading { get; private set; }

        /// <summary>
        /// Сводка последней успешной загрузки — для оверлея и приёмки F7
        /// (хотфикс 0.6.4: «тело доехало» должно быть видно без Hierarchy).
        /// </summary>
        public BodyInfo LastBody { get; private set; }

        /// <summary>Вызывается после успешной загрузки: (персона, модель).</summary>
        public event Action<string, GameObject> Loaded;

        /// <summary>Вызывается при ошибке: (персона, причина).</summary>
        public event Action<string, VrmLoadResult> Failed;

        private Coroutine _running;
        private int _generation;
        private string _loadingPersona = "";

        /// <summary>Чьё тело грузится прямо сейчас (для гварда повторного свопа).</summary>
        public string LoadingPersona
        {
            get { return _loadingPersona; }
        }

        /// <summary>
        /// Горячий своп тела персоны.
        ///
        /// Хотфикс 0.6.4 (блокер F7 «тело не доезжает»):
        /// * если <paramref name="serverRelativePath"/> пуст, URL строится САМ
        ///   (<see cref="ModelUrlFor"/>) — эндпоинт модели существует отдельно от кадра
        ///   persona (D5.4/ADR-017), поэтому «сервер не прислал vrm» больше не тупик;
        /// * гвард идемпотентности: та же персона уже на сцене → повторной загрузки нет
        ///   (hello и persona приходят подряд, без гварда тело качалось бы дважды);
        /// * старт/финиш/отказ логируются безусловно, а не под <c>config.verbose</c>.
        /// </summary>
        /// <param name="personaId">Id персоны.</param>
        /// <param name="localPath">Локальный путь к .vrm (из face.yaml); может быть пустым.</param>
        /// <param name="serverRelativePath">Относительный URL на сервере (из кадра persona.vrm); пусто → построим сами.</param>
        /// <param name="force">Перезагрузить тело, даже если эта персона уже на сцене.</param>
        public void Swap(string personaId, string localPath, string serverRelativePath = "", bool force = false)
        {
            if (!force && !Loading && Model != null &&
                !string.IsNullOrEmpty(personaId) && CurrentPersona == personaId)
            {
                Debug.Log($"[Lilith] тело: '{personaId}' уже на сцене — повторный своп не нужен (force=false)");
                return;
            }

            // Хотфикс 0.6.4: сервер шлёт hello ДВАЖДЫ (на accept и в ответ на hello
            // клиента), поэтому без этого гварда одно и то же тело качалось бы дважды
            // подряд — на 20 МБ это выглядит как «загрузка зависла».
            if (!force && Loading && !string.IsNullOrEmpty(personaId) && _loadingPersona == personaId)
            {
                Debug.Log($"[Lilith] тело: '{personaId}' уже грузится — повторный своп пропущен (force=false)");
                return;
            }

            var relative = serverRelativePath;
            var source = "URL из кадра persona";
            if (string.IsNullOrEmpty(relative))
            {
                relative = ModelUrlFor(personaId);
                source = "URL построен клиентом";
            }

            if (Loading && _running != null)
            {
                StopCoroutine(_running);
            }

            _loadingPersona = personaId ?? "";
            _generation++;
            _running = StartCoroutine(SwapRoutine(personaId, localPath, relative, _generation, source));
        }

        /// <summary>
        /// URL тела персоны на сервере: <c>{ServerBaseUrl}/api/face/personas/{id}/model.vrm</c>.
        /// Хотфикс 0.6.4: клиент умеет построить его сам, не дожидаясь поля <c>vrm</c> в кадре.
        /// </summary>
        public string ModelUrlFor(string personaId)
        {
            return BuildModelUrl(serverBaseUrl, personaId);
        }

        /// <summary>
        /// Чистая функция построения URL тела (проверяется гвардами без сцены и без сети).
        /// Лишние слэши на стыке base и path не появляются, id экранируется.
        /// </summary>
        public static string BuildModelUrl(string baseUrl, string personaId)
        {
            if (string.IsNullOrEmpty(personaId))
            {
                return "";
            }

            var root = (baseUrl ?? "").Trim().TrimEnd('/');
            return root + "/api/face/personas/" + Uri.EscapeDataString(personaId.Trim()) + "/model.vrm";
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
            _loadingPersona = "";
            LastBody = default;
        }

        private IEnumerator SwapRoutine(
            string personaId,
            string localPath,
            string serverRelativePath,
            int generation,
            string urlSource = "")
        {
            Loading = true;
            LastError = "";
            var startedAt = Time.realtimeSinceStartup;

            byte[] bytes = null;
            // Хотфикс 0.6.5: loadFromServerOnly форсирует HTTP-ветку, даже если кадр
            // persona принёс face.vrm_path (у Кирюши там локальный оверрайд).
            var path = loadFromServerOnly ? "" : ResolveLocalPath(localPath);
            if (loadFromServerOnly && !string.IsNullOrEmpty(localPath))
            {
                Debug.Log($"[Lilith] тело: фаза 0 — loadFromServerOnly=true, локальный путь '{localPath}' проигнорирован");
            }

            if (!string.IsNullOrEmpty(path) && File.Exists(path))
            {
                Debug.Log($"[Lilith] тело: старт свопа '{personaId}' ← файл {path}");
                bytes = File.ReadAllBytes(path);
                Debug.Log($"[Lilith] тело: фаза 1 — байты прочитаны с диска: {bytes.Length} Б");
            }
            else if (!string.IsNullOrEmpty(serverRelativePath))
            {
                var url = BuildUrl(serverRelativePath);
                Debug.Log($"[Lilith] тело: старт свопа '{personaId}' ← GET {url} ({urlSource})");
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
                        Debug.LogWarning($"[Lilith] тело: ОТКАЗ '{personaId}' — не скачать {url}: {request.error}");
                        Failed?.Invoke(personaId, VrmLoadResult.DownloadFailed);
                        yield break;
                    }

                    bytes = request.downloadHandler.data;
                    Debug.Log($"[Lilith] тело: фаза 1 — скачано {(bytes != null ? bytes.Length : 0)} Б с {url}");
                }
            }
            else
            {
                Loading = false;
                LastError = "нет ни локального пути, ни id персоны: URL тела построить не из чего";
                Debug.LogWarning($"[Lilith] тело: ОТКАЗ — {LastError}. " +
                                 "Проверь Server Base Url и id персоны (кадр hello.persona)");
                Failed?.Invoke(personaId, VrmLoadResult.NotFound);
                yield break;
            }

            if (bytes == null || bytes.Length < 4)
            {
                Loading = false;
                LastError = "пустой файл модели";
                Debug.LogWarning($"[Lilith] тело: ОТКАЗ '{personaId}' — {LastError} ({(bytes != null ? bytes.Length : 0)} Б)");
                Failed?.Invoke(personaId, VrmLoadResult.NotFound);
                yield break;
            }

            if (generation != _generation)
            {
                // Пока качали, пришёл новый своп — этот уже не нужен.
                Loading = false;
                Debug.Log($"[Lilith] тело: своп '{personaId}' устарел (generation {generation} → {_generation})");
                yield break;
            }

#if LILITH_UNIVRM
            // Хотфикс 0.6.3 (CS4032): SwapRoutine — IEnumerator-корутина, а `await`
            // в ней недопустим. Задачу заводим отдельно и ждём через `yield return task`:
            // Unity умеет ждать Task внутри корутины, а исключения достаём из самой
            // задачи (иначе они улетят в UnobservedTaskException и тело «молча» не загрузится).
            Vrm10Instance instance = null;
            System.Threading.Tasks.Task<Vrm10Instance> loadTask = null;
            try
            {
                // RuntimeOnlyAwaitCaller поддерживает только Play Mode: вне игры его
                // NextFrameTaskScheduler бросает NotSupportedException (проверено по
                // исходникам UniVRM 0.131.2). Ловим это отдельной веткой — см. ниже.
                //
                // Хотфикс 0.6.5: ImmediateCaller (UniGLTF v0.131.2, сверено по исходникам:
                // Packages/UniGLTF/Runtime/Utils/AwaitCaller/ImmediateCaller.cs —
                // `public sealed class ImmediateCaller : IAwaitCaller`, «синхронное
                // исполнение»: NextFrame/Run/Run<T> возвращают уже завершённые Task)
                // НЕ зависит от next-frame планировщика, поэтому дедлок с ним
                // невозможен конструктивно. Это и рабочий режим для мелких тел,
                // и диагноз: если с ImmediateCaller грузится, а с RuntimeOnly нет —
                // виноват планировщик кадров, а не байты модели.
                IAwaitCaller awaitCaller = useImmediateAwaitCaller
                    ? new ImmediateCaller()
                    : new RuntimeOnlyAwaitCaller(awaitTimeoutSeconds);
                Debug.Log($"[Lilith] тело: фаза 2 — awaitCaller: {(useImmediateAwaitCaller ? "ImmediateCaller (синхронно, один кадр)" : $"RuntimeOnlyAwaitCaller({awaitTimeoutSeconds})")}");

                loadTask = Vrm10.LoadBytesAsync(
                    bytes,
                    canLoadVrm0X: true,
                    controlRigGenerationOption: ControlRigGenerationOption.Generate,
                    showMeshes: true,
                    awaitCaller: awaitCaller);
                Debug.Log($"[Lilith] тело: фаза 3 — задача LoadBytesAsync создана, {(loadTask != null && loadTask.IsCompleted ? "уже завершена" : "ждём завершения")}");
            }
            catch (NotSupportedException notSupported)
            {
                Loading = false;
                LastError = "загрузка VRM работает только в Play Mode (RuntimeOnlyAwaitCaller): " + notSupported.Message;
                Debug.LogError("[Lilith] " + LastError);
                Failed?.Invoke(personaId, VrmLoadResult.LoadFailed);
                yield break;
            }
            catch (Exception ex)
            {
                Loading = false;
                LastError = ex.Message;
                Debug.LogError($"[Lilith] VRM не запустился на загрузку: {ex}");
                Failed?.Invoke(personaId, VrmLoadResult.LoadFailed);
                yield break;
            }

            // Хотфикс 0.6.5 (ДЕДЛОК): `yield return loadTask` в Unity 6000.0 не ждёт
            // Task — из корутины поддерживается только Awaitable (generic Awaitable<T>
            // и Task — нет, см. Manual «Write and run coroutines»). Незнакомый объект
            // трактуется как «один кадр», поэтому следующий шаг брал `loadTask.Result`
            // у незавершённой задачи: `.Result` блокирует главный поток, а продолжения
            // UniVRM enqueue'ятся в NextFrameTaskScheduler → UnityLoopTaskScheduler.Update()
            // (тот же главный поток). Итог: задача не может завершиться никогда,
            // редактор виснет намертво. Ждём по кадру и НЕ блокируем поток.
            var waitStarted = Time.realtimeSinceStartup;
            var waitFrames = 0;
            var nextReport = 0.5f;
            while (!loadTask.IsCompleted)
            {
                yield return null;
                waitFrames++;
                var waited = Time.realtimeSinceStartup - waitStarted;
                if (waited >= nextReport)
                {
                    nextReport += 0.5f;
                    Debug.Log($"[Lilith] тело: фаза 4 — ждём LoadBytesAsync: {waited:F1} с, {waitFrames} кадров (главный поток не блокирован)");
                }

                if (loadTimeoutSeconds > 0f && waited >= loadTimeoutSeconds)
                {
                    Loading = false;
                    LastError = $"загрузка VRM не завершилась за {loadTimeoutSeconds:F0} с ({waitFrames} кадров ожидания)";
                    Debug.LogError($"[Lilith] тело: ОТКАЗ '{personaId}' — {LastError}. " +
                                   "Задача брошена незавершённой (если она завершится позже, в сцене может " +
                                   "появиться объект-сирота — снеси его руками). Что попробовать: " +
                                   "useImmediateAwaitCaller=true (загрузка в одном кадре, без next-frame " +
                                   "планировщика) и/или loadFromServerOnly=true.");
                    Failed?.Invoke(personaId, VrmLoadResult.LoadFailed);
                    yield break;
                }
            }

            Debug.Log($"[Lilith] тело: фаза 5 — задача завершена: {waitFrames} кадров, {(Time.realtimeSinceStartup - waitStarted) * 1000f:F0} мс");

            if (loadTask.IsFaulted)
            {
                var cause = loadTask.Exception?.GetBaseException() ?? loadTask.Exception;
                Loading = false;
                LastError = cause?.Message ?? "неизвестная ошибка загрузки";
                Debug.LogError($"[Lilith] VRM не загрузился: {cause}");
                Failed?.Invoke(personaId, VrmLoadResult.LoadFailed);
                yield break;
            }

            if (loadTask.IsCanceled)
            {
                Loading = false;
                LastError = "загрузка VRM отменена";
                Debug.LogWarning($"[Lilith] тело: ОТКАЗ '{personaId}' — {LastError}");
                Failed?.Invoke(personaId, VrmLoadResult.LoadFailed);
                yield break;
            }

            // Безопасно: цикл выше выходит только при IsCompleted, а ветки IsFaulted
            // и IsCanceled уже разобраны — блокировки на `.Result` быть не может.
            instance = loadTask.Result;

            if (generation != _generation)
            {
                // Пока тело грузилось, пришёл новый своп: этот результат уже не нужен.
                if (instance != null)
                {
                    Destroy(instance.gameObject);
                }

                Loading = false;
                Debug.Log($"[Lilith] тело: своп '{personaId}' устарел после загрузки (generation {generation} → {_generation})");
                yield break;
            }

            if (instance == null)
            {
                Loading = false;
                LastError = "Vrm10.LoadBytesAsync вернул null";
                Debug.LogError($"[Lilith] тело: ОТКАЗ '{personaId}' — {LastError} (байт: {bytes.Length})");
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
            LastBody = new BodyInfo(
                personaId,
                bytes.Length,
                Time.realtimeSinceStartup - startedAt,
                Model.GetComponentsInChildren<Transform>().Length,
                root != null ? root.name : "(без root)");
            Debug.Log(
                $"[Lilith] тело: фаза 6 — ГОТОВО '{personaId}' за {LastBody.ElapsedSeconds * 1000f:F0} мс · " +
                $"{LastBody.Bytes / 1048576f:F2} МБ · трансформов {LastBody.Transforms} · " +
                $"родитель {LastBody.Parent} · детей у родителя {(root != null ? root.childCount : 0)}");
            Loaded?.Invoke(personaId, Model);
#else
            // Без UniVRM загрузка невозможна, но и «падать» клиент не должен:
            // сообщаем понятно, чтобы Кирюша знал, что делать.
            DestroyModel();
            Loading = false;
            LastError = "LILITH_UNIVRM не определён: установи UniVRM и добавь символ в Scripting Define Symbols";
            Debug.LogError($"[Lilith] тело: ОТКАЗ '{personaId}' — {LastError} " +
                           $"(источник: {urlSource}, байт получено {(bytes != null ? bytes.Length : 0)}, " +
                           $"{(Time.realtimeSinceStartup - startedAt) * 1000f:F0} мс)");
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

        /// <summary>
        /// Сообщить подписчикам, что тело загружено и привязано.
        ///
        /// Хотфикс 0.6.3 (CS0067): normally событие дёргается из <c>#if LILITH_UNIVRM</c>-ветки,
        /// и если символ не определён (или ветка не скомпилировалась), компилятор считает
        /// событие неиспользуемым. Метод даёт легальную точку вызова из любого кода —
        /// например, если тело поставили в сцену руками в Editor'е и его надо привязать
        /// к <see cref="FaceRig"/> без загрузки файла.
        /// </summary>
        /// <param name="personaId">Чьё тело.</param>
        /// <param name="model">GameObject с Vrm10Instance.</param>
        public void NotifyLoaded(string personaId, GameObject model)
        {
            if (model == null)
            {
                return;
            }

            Model = model;
            CurrentPersona = personaId ?? "";
            Loaded?.Invoke(CurrentPersona, model);
        }

        /// <summary>То же для ошибки — чтобы подписчики узнали о провале из любого пути.</summary>
        public void NotifyFailed(string personaId, VrmLoadResult result, string reason = "")
        {
            if (!string.IsNullOrEmpty(reason))
            {
                LastError = reason;
            }

            Failed?.Invoke(personaId, result);
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
