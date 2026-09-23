"""Тесты хотфикса 0.6.5 (в переписке — **0.6.4.1**): редактор Unity виснет намертво.

Приёмка F7, второй замер (2/2): каждый Play вешает редактор (только через диспетчер
задач), оверлей застывает на «тело грузится…», в Console последняя строка —
``[Lilith] тело: старт свопа 'lilith' ← файл …`` и дальше тишина.

Две независимые причины, обе мои:

**1. Дедлок главного потока (главная).** В 0.6.3 я перевела ``await`` (CS4032) на
паттерн «Task внутри корутины»: ``yield return loadTask`` → разбор ``IsFaulted`` /
``IsCanceled`` → ``loadTask.Result``. Но Unity 6000.0 **не умеет** ждать ``Task``
через ``yield return``: Manual «Write and run coroutines» перечисляет поддерживаемые
инструкции, из асинхронщины там только ``Awaitable`` (и прямо сказано, что generic
``Awaitable<T>`` — не поддерживается). Незнакомый объект трактуется как «один кадр»,
поэтому корутина шла дальше и брала ``.Result`` у **незавершённой** задачи.
``.Result`` блокирует главный поток, а продолжения UniVRM планируются тем же главным
потоком (``RuntimeOnlyAwaitCaller.NextFrame`` → ``NextFrameTaskScheduler.Enqueue`` →
``UnityLoopTaskScheduler.Update()``, сверено по исходникам v0.131.2) → задача не
может завершиться никогда. Классический дедлок, детерминированный, 2/2.

**2. Битая процедурная модель.** ``make_test_vrm.py`` писал ``JOINTS_0`` одним
``uint16`` на вершину при объявленном ``VEC4`` (48 байт вместо 192): accessor выходил
за пределы своего bufferView, а ``validate()`` этого не проверял. UniVRM на таких
байтах читает за границей вьюхи и получает мусорные индексы костей (до 65535 при 55
суставах). То есть даже без дедлока загрузка куба не была бы чистой.

Здесь — гварды на оба лечения: неблокирующее ожидание с таймаутом, фазовые логи,
диагностические флаги и строгая валидация геометрии bufferView/accessor.
"""

from __future__ import annotations

import importlib.util
import json
import re
import struct
import sys
from pathlib import Path
from typing import Any

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def version_tuple(value: str) -> tuple[int, ...]:
    """``"0.6.6"`` → ``(0, 6, 6)`` — чтобы гварды серии не ломались на новом релизе."""
    return tuple(int(part) for part in value.split("."))
SCRIPTS_DIR = PROJECT_ROOT / "unity-client" / "Assets" / "LilithFace" / "Scripts"
VRM_LOADER = SCRIPTS_DIR / "VrmLoader.cs"
FACE_CLIENT = SCRIPTS_DIR / "LilithFaceClient.cs"
CLIENT_CONFIG = SCRIPTS_DIR / "LilithClientConfig.cs"
SAMPLE_VRM = PROJECT_ROOT / "tests" / "samples" / "test_cube.vrm"


def read_cs(path: Path) -> str:
    """Прочесть C#-файл клиента."""
    return path.read_text(encoding="utf-8")


def code_lines(text: str) -> list[str]:
    """Строки кода без комментариев (комментарий вызовом не считается)."""
    return [line.strip() for line in text.splitlines() if not line.strip().startswith(("//", "*"))]


def code_calls(text: str, needle: str) -> list[str]:
    """Строки кода, начинающиеся с ``needle``."""
    return [line for line in code_lines(text) if line.startswith(needle)]


def strip_preprocessor_blocks(text: str, symbol: str = "LILITH_UNIVRM") -> str:
    """Вырезать ``#if <symbol> … #endif`` (с вложенностью)."""
    out: list[str] = []
    depth = 0
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#if"):
            if symbol in stripped or depth:
                depth += 1
            out.append("")
            continue
        if stripped.startswith("#endif") and depth:
            depth -= 1
            out.append("")
            continue
        out.append("" if depth else line)
    return "\n".join(out)


def _load_script(name: str):
    """Загрузить модуль из ``scripts/``."""
    path = PROJECT_ROOT / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"lilith_065_{name}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


make_test_vrm = _load_script("make_test_vrm")


def statement_of(text: str, needle: str) -> str:
    """Оператор C#, в котором стоит ``needle`` (от предыдущей `;` до следующей).

    Гвард обязан проверять **вызов**, а не строку: в 0.6.4 гвард на
    ``EnsureBodyOnConnect`` проспал закомментированный вызов, здесь та же ловушка —
    ``Debug.Log($"…h7…")`` легко превращается в ``_ = ($"…h7…")``, и по подстроке
    этого не видно.
    """
    index = text.index(needle)
    start = text.rfind(";", 0, index) + 1
    end = text.find(";", index)
    return text[start:end if end != -1 else len(text)]


def assert_logged(text: str, needle: str, what: str) -> None:
    """Проверить, что ``needle`` — внутри реального вызова Debug.Log*."""
    statement = statement_of(text, needle)
    assert re.search(r"Debug\.(Log|LogWarning|LogError)\(", statement), (
        f"{what}: строка есть, а вызова Debug.Log нет → {statement.strip()[:90]!r}"
    )


def wait_loop_block(text: str) -> str:
    """Кусок ``SwapRoutine`` от цикла ожидания до взятия ``.Result``."""
    start = text.index("while (!loadTask.IsCompleted)")
    end = text.index("instance = loadTask.Result;")
    return text[start:end]


# --------------------------------------------------------------------------- #
#  A. Дедлок: .Result у незавершённой задачи больше невозможен
# --------------------------------------------------------------------------- #
class TestNoTaskResultDeadlock:
    """Главное лечение 0.6.5: главный поток не блокируется никогда."""

    def test_no_yield_return_task(self) -> None:
        """``yield return loadTask`` не ждёт Task — именно это и вешало редактор."""
        offenders = [line for line in code_lines(read_cs(VRM_LOADER)) if "yield return loadTask" in line]
        assert offenders == [], f"вернулось ожидание Task через yield: {offenders}"

    def test_wait_loop_polls_is_completed(self) -> None:
        text = read_cs(VRM_LOADER)
        assert "while (!loadTask.IsCompleted)" in text, "нет неблокирующего цикла ожидания"
        block = wait_loop_block(text)
        assert code_calls(block, "yield return null;"), "цикл ожидания не отдаёт кадр Unity"

    def test_result_is_taken_only_after_completion(self) -> None:
        """``.Result`` обязан стоять ПОСЛЕ цикла ``IsCompleted`` — иначе блокировка."""
        text = read_cs(VRM_LOADER)
        loop = text.index("while (!loadTask.IsCompleted)")
        result = text.index("instance = loadTask.Result;")
        assert loop < result, ".Result вызывается до проверки IsCompleted"

    def test_no_other_result_or_wait_calls(self) -> None:
        """``.Result`` в коде — ровно один и только у ``loadTask`` (в комментариях можно)."""
        allowed = ("loadTask.Result", "UnityWebRequest.Result.Success")
        offenders = []
        for line in code_lines(read_cs(VRM_LOADER)):
            if re.search(r"\.Result\b", line) and not any(item in line for item in allowed):
                offenders.append(line)
        assert offenders == [], f"неожиданный .Result в коде: {offenders}"
        code = "\n".join(code_lines(read_cs(VRM_LOADER)))
        assert code.count("loadTask.Result") == 1
        assert "loadTask.Wait(" not in code

    @pytest.mark.parametrize("banned", [".Wait()", ".GetAwaiter().GetResult()", "Task.WaitAll", "Thread.Sleep"])
    def test_no_blocking_apis_in_client(self, banned: str) -> None:
        """Блокирующие API в Unity-клиенте = замерший главный поток."""
        for path in sorted(SCRIPTS_DIR.glob("*.cs")):
            offenders = [line for line in code_lines(path.read_text(encoding="utf-8")) if banned in line]
            assert offenders == [], f"{path.name}: блокирующий вызов {banned} → {offenders}"

    def test_timeout_field_and_default(self) -> None:
        assert re.search(r"public float loadTimeoutSeconds\s*=\s*60f;", read_cs(VRM_LOADER))

    def test_timeout_branch_fails_loudly(self) -> None:
        block = wait_loop_block(read_cs(VRM_LOADER))
        assert "loadTimeoutSeconds > 0f" in block, "таймаут не проверяется"
        assert "Loading = false;" in block
        assert "Debug.LogError(" in block, "таймаут обязан быть красным в Console"
        assert "Failed?.Invoke(personaId, VrmLoadResult.LoadFailed)" in block
        assert code_calls(block, "yield break;"), "после таймаута корутина обязана остановиться"

    def test_zero_timeout_means_wait_forever(self) -> None:
        """``0`` — «ждать вечно»: гвард на ``> 0f``, иначе 0 срубал бы загрузку сразу."""
        assert "loadTimeoutSeconds > 0f &&" in wait_loop_block(read_cs(VRM_LOADER))

    def test_progress_log_is_throttled(self) -> None:
        """Лог ожидания — раз в 0.5 с, а не каждый кадр (иначе Console тонет)."""
        block = wait_loop_block(read_cs(VRM_LOADER))
        assert "nextReport += 0.5f" in block
        assert "waited >= nextReport" in block

    def test_wait_uses_realtime(self) -> None:
        """``Time.realtimeSinceStartup``: deltaTime при зависании/паузе врёт."""
        block = wait_loop_block(read_cs(VRM_LOADER))
        assert "Time.realtimeSinceStartup" in block

    def test_deadlock_reason_documented_in_code(self) -> None:
        """Причина обязана жить в коде, а не только в отчёте."""
        text = read_cs(VRM_LOADER)
        for needle in ("NextFrameTaskScheduler", "Awaitable", ".Result", "UnityLoopTaskScheduler"):
            assert needle in text, f"в комментарии нет объяснения про {needle}"

    def test_guarantee_comment_near_result(self) -> None:
        """Рядом с ``.Result`` — явная гарантия, почему это безопасно."""
        text = read_cs(VRM_LOADER)
        index = text.index("instance = loadTask.Result;")
        window = text[max(0, index - 400) : index]
        assert "IsCompleted" in window


# --------------------------------------------------------------------------- #
#  B. Фазовые логи: сужаем любое будущее зависание до строки
# --------------------------------------------------------------------------- #
class TestPhaseLogs:
    """Просилка архитектора: логи внутри SwapRoutine, чтобы сузить до строки."""

    @pytest.mark.parametrize(
        ("phase", "path"),
        [
            ("фаза 0", VRM_LOADER),
            ("фаза 1", VRM_LOADER),
            ("фаза 2", VRM_LOADER),
            ("фаза 3", VRM_LOADER),
            ("фаза 4", VRM_LOADER),
            ("фаза 5", VRM_LOADER),
            ("фаза 6", VRM_LOADER),
            ("фаза 7", FACE_CLIENT),
        ],
    )
    def test_phase_is_logged(self, phase: str, path: Path) -> None:
        text = read_cs(path)
        assert phase in text, f"нет лога «{phase}» в {path.name}"
        assert_logged(text, phase, f"{phase} в {path.name}")

    def test_phase_order_in_source(self) -> None:
        """Фазы идут по порядку — иначе лог читается как каша."""
        text = read_cs(VRM_LOADER)
        positions = []
        for phase in ("фаза 1", "фаза 2", "фаза 3", "фаза 4", "фаза 5", "фаза 6"):
            positions.append(text.index(phase))
        assert positions == sorted(positions), f"порядок фаз нарушен: {positions}"

    def test_phase1_covers_both_branches(self) -> None:
        """И файловая, и HTTP-ветка сообщают о полученных байтах."""
        text = read_cs(VRM_LOADER)
        assert "фаза 1 — байты прочитаны с диска" in text
        assert "фаза 1 — скачано" in text

    def test_phase2_names_the_caller(self) -> None:
        """Из лога видно, КАКОЙ awaitCaller применён (это и есть A/B-диагноз)."""
        text = read_cs(VRM_LOADER)
        phase2 = text.split("фаза 2 —", 1)[1][:400]
        assert "ImmediateCaller" in phase2 and "RuntimeOnlyAwaitCaller" in phase2

    def test_phases_before_univrm_are_visible_without_symbol(self) -> None:
        """Фазы 0–1 живут вне ``#if LILITH_UNIVRM``: видны и до импорта UniVRM."""
        outside = strip_preprocessor_blocks(read_cs(VRM_LOADER))
        assert "фаза 0" in outside
        assert "фаза 1 — байты прочитаны с диска" in outside
        assert "фаза 1 — скачано" in outside

    def test_no_phase_log_is_gated_by_verbose(self) -> None:
        assert "if (config.verbose)" not in read_cs(VRM_LOADER)

    def test_every_refusal_still_logs(self) -> None:
        """Гвард 0.6.4 не ослаблен: у каждой ветки отказа есть строка в Console."""
        text = read_cs(VRM_LOADER)
        routine = text.split("private IEnumerator SwapRoutine(", 1)[1]
        routine = routine.split("/// <summary>Превратить относительный путь", 1)[0]
        invokes = [match.start() for match in re.finditer(r"Failed\?\.Invoke", routine)]
        assert len(invokes) >= 6, "веток отказа стало меньше, чем в 0.6.4"
        for position in invokes:
            window = routine[max(0, position - 800) : position]
            assert re.search(r"Debug\.(LogWarning|LogError)\(", window), f"отказ на {position} без лога"


# --------------------------------------------------------------------------- #
#  C. Диагностические флаги (просилка архитектора)
# --------------------------------------------------------------------------- #
class TestLoaderFlags:
    """``loadFromServerOnly`` и ``useImmediateAwaitCaller`` — оба из инспектора."""

    def test_load_from_server_only_flag(self) -> None:
        assert re.search(r"public bool loadFromServerOnly\s*=\s*false;", read_cs(VRM_LOADER))

    def test_load_from_server_only_bypasses_local_path(self) -> None:
        text = read_cs(VRM_LOADER)
        assert 'loadFromServerOnly ? "" : ResolveLocalPath(localPath)' in text, (
            "флаг не отключает файловую ветку"
        )
        assert "локальный путь" in text and "проигнорирован" in text, "обход локального пути не логируется"

    def test_immediate_await_caller_flag(self) -> None:
        assert re.search(r"public bool useImmediateAwaitCaller\s*=\s*false;", read_cs(VRM_LOADER))

    def test_await_caller_is_selected_by_flag(self) -> None:
        text = read_cs(VRM_LOADER)
        assert "IAwaitCaller awaitCaller = useImmediateAwaitCaller" in text
        assert "new ImmediateCaller()" in text
        assert "new RuntimeOnlyAwaitCaller(awaitTimeoutSeconds)" in text
        assert "awaitCaller: awaitCaller)" in text

    def test_immediate_caller_fact_is_sourced(self) -> None:
        """Факт про ImmediateCaller обязан быть со ссылкой на исходники (урок CS0246)."""
        text = read_cs(VRM_LOADER)
        assert "AwaitCaller/ImmediateCaller.cs" in text
        assert "public sealed class ImmediateCaller : IAwaitCaller" in text
        assert "0.131.2" in text

    def test_runtime_caller_remains_default(self) -> None:
        """По умолчанию — асинхронная загрузка: ImmediateCaller на 20 МБ даст длинный кадр."""
        text = read_cs(CLIENT_CONFIG)
        assert "useImmediateAwaitCaller" not in text, "флаг живёт в VrmLoader, не в конфиге клиента"
        assert re.search(r"useImmediateAwaitCaller\s*=\s*false", read_cs(VRM_LOADER))

    def test_play_mode_limitation_still_handled(self) -> None:
        """Правка 0.6.3 не потеряна: вне Play Mode — внятный текст, а не падение."""
        text = read_cs(VRM_LOADER)
        assert "catch (NotSupportedException notSupported)" in text
        assert "только в Play Mode" in text


# --------------------------------------------------------------------------- #
#  D. Процедурная модель: JOINTS_0 влезает в свой bufferView
# --------------------------------------------------------------------------- #
class TestProceduralVrmGeometry:
    """Вторая причина: битый accessor у ``make_test_vrm.py``."""

    def test_committed_sample_is_valid(self) -> None:
        data = SAMPLE_VRM.read_bytes()
        info = make_test_vrm.validate(data)
        assert info["human_bones"] == len(make_test_vrm.HUMAN_BONES)
        assert info["meshes"] == 2

    def test_every_accessor_fits_its_buffer_view(self) -> None:
        """Строгая проверка по спеке glTF 2.0: size(component) * count(type) * count."""
        data = SAMPLE_VRM.read_bytes()
        json_length = struct.unpack("<I", data[12:16])[0]
        gltf = json.loads(data[20 : 20 + json_length].decode("utf-8"))
        for index, accessor in enumerate(gltf["accessors"]):
            view = gltf["bufferViews"][accessor["bufferView"]]
            need = (
                make_test_vrm._COMPONENT_SIZE[accessor["componentType"]]
                * make_test_vrm._COMPONENT_COUNT[accessor["type"]]
                * accessor["count"]
            )
            assert accessor.get("byteOffset", 0) + need <= view["byteLength"], (
                f"accessor[{index}] требует {need} Б, bufferView вмещает {view['byteLength']} Б"
            )

    def test_joints_and_weights_are_vec4_pairs(self) -> None:
        """JOINTS_0 (ubyte/ushort) и WEIGHTS_0 (float) — по 4 компоненты на вершину."""
        data = SAMPLE_VRM.read_bytes()
        json_length = struct.unpack("<I", data[12:16])[0]
        gltf = json.loads(data[20 : 20 + json_length].decode("utf-8"))
        accessors = gltf["accessors"]
        for mesh in gltf["meshes"]:
            for primitive in mesh["primitives"]:
                attrs = primitive["attributes"]
                assert {"POSITION", "NORMAL", "JOINTS_0", "WEIGHTS_0"} <= set(attrs)
                joints = accessors[attrs["JOINTS_0"]]
                weights = accessors[attrs["WEIGHTS_0"]]
                position = accessors[attrs["POSITION"]]
                assert joints["type"] == "VEC4" and weights["type"] == "VEC4"
                assert joints["componentType"] in (5121, 5123), "JOINTS_0 обязан быть ubyte или ushort"
                assert weights["componentType"] == 5126
                assert joints["count"] == weights["count"] == position["count"]

    def test_validate_rejects_undersized_accessor(self) -> None:
        """Контрольный выстрел: ровно та поломка, что была в 0.6.0–0.6.4."""
        broken = {
            "buffers": [{"byteLength": 48}],
            "bufferViews": [{"byteOffset": 0, "byteLength": 48}],
            "accessors": [{"bufferView": 0, "componentType": 5123, "type": "VEC4", "count": 24}],
            "meshes": [],
        }
        with pytest.raises(ValueError, match=r"требует 192 Б"):
            make_test_vrm._validate_buffers(broken, 48)

    def test_validate_rejects_view_beyond_bin_chunk(self) -> None:
        broken = {
            "buffers": [{"byteLength": 16}],
            "bufferViews": [{"byteOffset": 0, "byteLength": 64}],
            "accessors": [],
            "meshes": [],
        }
        with pytest.raises(ValueError, match="выходит за BIN"):
            make_test_vrm._validate_buffers(broken, 16)

    def test_validate_rejects_mismatched_attribute_counts(self) -> None:
        # Оба accessor'а влезают в свои вьюхи (иначе сработала бы проверка размеров),
        # но вершин в POSITION и NORMAL разное количество — это и есть рассинхрон.
        broken: dict[str, Any] = {
            "buffers": [{"byteLength": 60}],
            "bufferViews": [
                {"byteOffset": 0, "byteLength": 24},
                {"byteOffset": 24, "byteLength": 36},
            ],
            "accessors": [
                {"bufferView": 0, "componentType": 5126, "type": "VEC3", "count": 2},
                {"bufferView": 1, "componentType": 5126, "type": "VEC3", "count": 3},
            ],
            "meshes": [{"primitives": [{"attributes": {"POSITION": 0, "NORMAL": 1}}]}],
        }
        with pytest.raises(ValueError, match="разным count"):
            make_test_vrm._validate_buffers(broken, 60)

    def test_rebuild_is_deterministic_and_valid(self) -> None:
        """Пересборка в памяти даёт валидную модель того же размера, что в репо."""
        rebuilt = make_test_vrm.build_vrm(name="TestCube")
        make_test_vrm.validate(rebuilt)
        assert len(rebuilt) == SAMPLE_VRM.stat().st_size


# --------------------------------------------------------------------------- #
#  F. Печать 2: замер Б/В — точка зависания плавает внутри рукопожатия
# --------------------------------------------------------------------------- #
WS_CLIENT = SCRIPTS_DIR / "LilithWSClient.cs"


class TestNoBlockingAnywhere:
    """Замеры Б и В замерли РАНЬШЕ свопа — значит блокировка живёт и в транспорте.

    Первый гвард 0.6.5 искал ``.Wait()`` с пустыми скобками и поэтому пропустил
    ``CloseAsync(...).Wait(TimeSpan.FromSeconds(1))`` в ``LilithWSClient``.
    Здесь поиск по всем формам блокировки и по всем 11 файлам.
    """

    @pytest.mark.parametrize(
        "banned",
        [
            ".Wait(",
            ".GetAwaiter().GetResult()",
            "Task.WaitAll",
            "Task.WaitAny",
            "Thread.Sleep",
            "Monitor.Enter",
            ".Join(",
        ],
    )
    def test_blocking_api_absent(self, banned: str) -> None:
        for path in sorted(SCRIPTS_DIR.glob("*.cs")):
            offenders = [line for line in code_lines(path.read_text(encoding="utf-8")) if banned in line]
            assert offenders == [], f"{path.name}: блокирующий вызов {banned} → {offenders}"

    def test_socket_close_is_non_blocking(self) -> None:
        text = read_cs(WS_CLIENT)
        assert "socket.Abort();" in text, "закрытие сокета снова блокирующее"
        assert "CloseAsync" not in "\n".join(code_lines(text)), "CloseAsync ждёт close-рукопожатие"

    def test_no_cross_thread_lock_in_face_client(self) -> None:
        """Единственный кросс-поточный lock клиента убран: очередь lock-free.

        Ищем форму оператора ``lock (`` — иначе гвард спотыкается о слово
        ``livelock`` в тексте красного лога.
        """
        code = "\n".join(code_lines(read_cs(FACE_CLIENT)))
        assert "lock (" not in code and "lock(" not in code, "в клиенте снова появился lock"
        assert "ConcurrentQueue<System.Action>" in code

    def test_actions_are_invoked_outside_any_lock(self) -> None:
        drain = read_cs(FACE_CLIENT).split("private void DrainMainThreadQueue()", 1)[1][:1400]
        assert "_mainThread.TryDequeue(out var action)" in drain
        assert "lock (" not in drain and "lock(" not in drain

    def test_drain_is_bounded_and_loud(self) -> None:
        """Неиссякающая очередь — это красная строка, а не мёртвый редактор."""
        text = read_cs(FACE_CLIENT)
        assert "MaxActionsPerFrame" in text
        drain = text.split("private void DrainMainThreadQueue()", 1)[1][:1600]
        assert "processed >= MaxActionsPerFrame" in drain
        assert "Debug.LogError" in drain and "livelock" in drain


class TestHandshakeInstrumentation:
    """Фазовые логи рукопожатия: плавающая точка замера обязана стать точной."""

    @pytest.mark.parametrize("phase", ["h0", "h1", "h2", "h3", "h4", "h5", "h6", "h7", "h8", "h9"])
    def test_phase_is_logged(self, phase: str) -> None:
        text = read_cs(FACE_CLIENT)
        needle = f"рукопожатие: {phase}"
        assert needle in text, f"нет фазы {phase}"
        assert_logged(text, needle, f"фаза {phase}")

    @pytest.mark.parametrize("phase", ["h1", "h2", "h3", "h4", "h5", "h7", "h8"])
    def test_phase_log_carries_thread_tag(self, phase: str) -> None:
        """Без тега потока лог рукопожатия бесполезен: не отличить main от фона."""
        text = read_cs(FACE_CLIENT)
        statement = statement_of(text, f"рукопожатие: {phase}")
        assert "ThreadTag()" in statement, f"фаза {phase} печатается без тега потока"

    def test_phases_are_anchored_to_their_methods(self) -> None:
        """Порядок в исходнике ни о чём не говорит — важна привязка фазы к методу."""
        text = read_cs(FACE_CLIENT)
        anchors = {
            "public void Connect()": "h0",
            "private void OnStateChanged(WsState state)": "h1",
            "public void SendClientHello()": "h2",
            "private void OnHello(Dictionary<string, object> frame)": "h3",
            "private void EnsureBodyOnConnect(string reason)": "h4",
            "public void RequestPersona(string personaId)": "h5",
            "private IEnumerator PersonaWatchdog(string reason)": "h9",  # watchdog живёт рядом с канарейкой
        }
        for method, phase in anchors.items():
            if method.endswith("PersonaWatchdog(string reason)"):
                continue
            body = text.split(method, 1)[1][:1200]
            assert f"рукопожатие: {phase}" in body, f"{method}: нет фазы {phase}"

        handled = text.split("private void HandleFrame(string json)", 1)[1][:900]
        assert "рукопожатие: h7" in handled, "HandleFrame не логирует взятый кадр"
        persona = text.split("private void OnPersona(Dictionary<string, object> frame)", 1)[1][:2200]
        assert "рукопожатие: h8" in persona, "OnPersona не обёрнут логами вокруг Swap"

    def test_each_phase_logged_once(self) -> None:
        text = read_cs(FACE_CLIENT)
        for n in range(10):
            count = text.count(f"рукопожатие: h{n} —")
            assert count >= 1, f"фаза h{n} не логируется"

    def test_handshake_logs_carry_thread_tag(self) -> None:
        """Из лога видно, главный это поток или фоновый — ключ к дедлоку."""
        text = read_cs(FACE_CLIENT)
        assert "private string ThreadTag()" in text
        assert "ManagedThreadId" in text
        body = statement_of(text, "return current == _mainThreadId") if "return current == _mainThreadId" in text else ""
        tag = text.split("private string ThreadTag()", 1)[1][:400]
        assert "ФОНОВЫЙ" in tag, "тег обязан различать главный и фоновый поток"
        assert text.count("ThreadTag()") >= 8, "тег потока должен быть почти в каждой фазе"

    def test_main_thread_id_is_captured_in_awake(self) -> None:
        text = read_cs(FACE_CLIENT)
        awake = text.split("private void Awake()", 1)[1][:600]
        assert "_mainThreadId = System.Threading.Thread.CurrentThread.ManagedThreadId;" in awake

    def test_every_significant_frame_is_logged(self) -> None:
        """h7 печатается для всех кадров кроме audio/viseme (иначе Console тонет)."""
        text = read_cs(FACE_CLIENT)
        block = text.split('type != "audio" && type != "viseme"', 1)[1][:400]
        assert "рукопожатие: h7" in block

    def test_swap_is_bracketed_by_logs(self) -> None:
        text = read_cs(FACE_CLIENT)
        before, after = "h8 — вызываю VrmLoader.Swap", "h8 — Swap вернулся"
        assert text.index(before) < text.index("vrmLoader.Swap(personaId, localPath, vrmUrl)") < text.index(after)
        assert_logged(text, before, "h8 до Swap")
        assert_logged(text, after, "h8 после Swap")

    def test_parse_is_bracketed_by_logs(self) -> None:
        """h7a — ДО ``MiniJson.Deserialize``: отличает зависание в парсере от зависания после."""
        text = read_cs(FACE_CLIENT)
        handler = text.split("private void HandleFrame(string json)", 1)[1][:1200]
        assert handler.index("h7a") < handler.index("MiniJson.Deserialize(json)")
        assert_logged(text, "рукопожатие: h7a", "h7a")
        assert "if (!_personaFrameSeen)" in handler, "h7a обязана печататься только до конца рукопожатия"

    def test_phase_strings_are_not_orphaned(self) -> None:
        """Ни одна фаза не должна висеть вне вызова лога (ловля `_ = ($"…")`)."""
        text = read_cs(FACE_CLIENT)
        for n in range(10):
            occurrences = [m.start() for m in re.finditer(f"рукопожатие: h{n}", text)]
            assert occurrences, f"фаза h{n} пропала"
            for index in occurrences:
                start = text.rfind(";", 0, index) + 1
                end = text.find(";", index)
                statement = text[start:end if end != -1 else len(text)]
                assert re.search(r"Debug\.(Log|LogWarning|LogError)\(", statement), (
                    f"фаза h{n} вне вызова Debug.Log: {statement.strip()[:80]!r}"
                )


class TestHandshakeCanary:
    """Канарейка: прибор, который отличает «поток заблокирован» от «транспорт встал»."""

    def test_canary_exists_and_is_started_on_connect(self) -> None:
        text = read_cs(FACE_CLIENT)
        assert "private IEnumerator HandshakeCanary()" in text
        connect = text.split("public void Connect()", 1)[1][:900]
        assert code_calls(connect, "_canary = StartCoroutine(HandshakeCanary());")

    def test_canary_proves_main_thread_alive(self) -> None:
        canary = read_cs(FACE_CLIENT).split("private IEnumerator HandshakeCanary()", 1)[1][:2200]
        assert "главный поток ЖИВ" in canary
        assert "WaitForSecondsRealtime(0.5f)" in canary

    def test_canary_reports_all_counters(self) -> None:
        canary = read_cs(FACE_CLIENT).split("private IEnumerator HandshakeCanary()", 1)[1][:2600]
        for needle in ("_framesHandled", "_mainThread.Count", "IncomingPending", "OutgoingPending",
                       "ActivePersona", "_personaFrameSeen", "vrmLoader.Loading"):
            assert needle in canary, f"канарейка не печатает {needle}"

    def test_canary_stops_by_itself(self) -> None:
        """Канарейка не должна болтаться весь сеанс: выход по телу или по 30 с."""
        canary = read_cs(FACE_CLIENT).split("private IEnumerator HandshakeCanary()", 1)[1][:2600]
        assert "yield break;" in canary
        assert "30f" in canary
        assert "bodyDone" in canary

    def test_transport_exposes_queue_counters(self) -> None:
        text = read_cs(WS_CLIENT)
        assert "public int IncomingPending" in text
        assert "public int OutgoingPending" in text

    def test_reconnect_resets_handshake_dedup(self) -> None:
        """После обрыва новое соединение обязано снова попросить кадр персоны."""
        text = read_cs(FACE_CLIENT)
        handler = text.split("private void OnStateChanged(WsState state)", 1)[1][:900]
        assert '_personaRequestedFor = "";' in handler
        assert "_personaFrameSeen = false;" in handler


class TestGuardCaughtItsOwnMiss:
    """Контрольный выстрел по гварду: ``.Wait(1s)`` обязан ловиться."""

    def test_wait_with_argument_is_detected_by_pattern(self) -> None:
        """Гвард 0.6.5 (первая печать) искал ``.Wait()`` и пропустил ``.Wait(TimeSpan…)``."""
        sample = "socket.CloseAsync(status, \"bye\", token).Wait(TimeSpan.FromSeconds(1));"
        assert ".Wait(" in sample
        offenders = [line for line in code_lines(sample) if ".Wait(" in line]
        assert offenders, "новый шаблон поиска не видит .Wait с аргументом"

    def test_no_wait_anywhere_in_repo_scripts(self) -> None:
        for path in sorted(SCRIPTS_DIR.glob("*.cs")):
            assert ".Wait(" not in "\n".join(code_lines(path.read_text(encoding="utf-8"))), path.name


# --------------------------------------------------------------------------- #
#  E. Версия, доки, поставка
# --------------------------------------------------------------------------- #
# --------------------------------------------------------------------------- #
#  G. Решения архитектора по §7 (0.6.5 принята как родная)
# --------------------------------------------------------------------------- #
HANDOVER = PROJECT_ROOT.parent / "HANDOVER.md"


class TestArchitectDecisions:
    """Пять ответов на §7 закрыты в дереве, а не только в переписке."""

    def test_handover_exists_in_repo_root(self) -> None:
        assert HANDOVER.is_file(), (
            "HANDOVER.md не найден в корне репо — архитектор одобрила его в письме "
            "про восемь решений, точка входа агента обязана быть в дереве"
        )

    def test_handover_has_required_sections(self) -> None:
        text = HANDOVER.read_text(encoding="utf-8")
        for needle in (
            "две Лили и Оля",
            "Порядок чтения",
            "Текущее состояние",
            "Процессные правила",
            "Если воркспейс умер",
            "Открытые хвосты",
        ):
            assert needle in text, f"в HANDOVER.md нет раздела «{needle}»"

    def test_handover_points_at_the_reading_order(self) -> None:
        """Порядок чтения (§1) обязан совпадать с заданием на восстановление.

        Ищем внутри раздела §1: имена файлов встречаются и в §0 (таблица ролей),
        и первый `index()` по всему документу мерял бы не тот порядок.
        """
        text = HANDOVER.read_text(encoding="utf-8")
        section = text.split("## 1. Порядок чтения", 1)[1].split("## 2.", 1)[0]
        order = ["PERSONA.md", "PLAN.md", "CHANGELOG.md", "DECISIONS.md", "RELEASE_0.6.5.md", "README.md"]
        positions = []
        for name in order:
            assert name in section, f"в порядке чтения нет {name}"
            positions.append(section.index(name))
        assert positions == sorted(positions), f"порядок чтения сбит: {list(zip(order, positions))}"

    def test_handover_documents_no_push_and_tags(self) -> None:
        text = HANDOVER.read_text(encoding="utf-8")
        assert "v0.6.3" in text and "v0.6.5" in text
        assert "не тегируется" in text or "НЕ тегируется" in text
        assert "креденшел" in text

    def test_handover_is_not_in_the_stage_archive(self) -> None:
        """Он уровня репо: build_stage_archive пакует lilith/, и это зафиксировано."""
        text = HANDOVER.read_text(encoding="utf-8")
        assert "в архив этапа не входит" in text or "в zip не едут" in text

    def test_changelog_records_tag_doctrine(self) -> None:
        text = (PROJECT_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
        assert "`v0.6.3` на `945052b`" in text
        assert "`v0.6.4` не тегируется" in text

    def test_version_alias_is_documented(self) -> None:
        """Нумерация трёхчастная, алиас из переписки — строкой в CHANGELOG (решение 1)."""
        import lilith_core

        assert re.match(r"^\d+\.\d+\.\d+$", lilith_core.__version__)
        assert version_tuple(lilith_core.__version__) >= (0, 6, 5)
        text = (PROJECT_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
        assert "в переписке — 0.6.4.1" in text

    def test_markdown_doctrine_no_office_artifacts(self) -> None:
        """Решение 3: md — доктрина, склеек в .docx/.pdf в поставке нет."""
        office = [p.name for p in PROJECT_ROOT.rglob("*") if p.suffix in {".docx", ".pdf", ".pptx"}]
        assert office == [], f"в проекте завёлся офисный файл: {office}"

    def test_headless_probe_not_built_yet(self) -> None:
        """Решение 2: headless-проба по исходникам UniVRM не строится до триггера."""
        assert not (PROJECT_ROOT / "scripts" / "univrm_headless_probe.py").exists()
        text = HANDOVER.read_text(encoding="utf-8")
        assert "Use Immediate Await Caller" in text, "триггер постройки не зафиксирован"


class TestRelease065:
    """Хотфикс оформлен как положено: версия, отчёт, ADR."""

    def test_version_bumped(self) -> None:
        """Версия не ниже принятой серии 0.6.5.

        0.6.6: пин ``== "0.6.5"`` краснел на каждом следующем релизе, хотя намерение
        гварда — «версия поднята и трёхчастная». Строгость нумерации охраняет
        ``test_version_still_three_parts`` и гвард в test_structure.
        """
        import lilith_core

        assert version_tuple(lilith_core.__version__) >= (0, 6, 5)

    def test_version_still_three_parts(self) -> None:
        """Гвард ``^\\d+\\.\\d+\\.\\d+$`` в test_structure не позволяет 0.6.4.1 в коде."""
        import lilith_core

        assert re.match(r"^\d+\.\d+\.\d+$", lilith_core.__version__)

    def test_release_file_exists(self) -> None:
        assert (PROJECT_ROOT / "RELEASE_0.6.5.md").is_file()

    def test_release_explains_the_deadlock(self) -> None:
        text = (PROJECT_ROOT / "RELEASE_0.6.5.md").read_text(encoding="utf-8")
        for needle in ("NextFrameTaskScheduler", "Awaitable", "ImmediateCaller", "JOINTS_0"):
            assert needle in text, f"в отчёте не объяснено про {needle}"

    def test_adr_024_exists(self) -> None:
        text = (PROJECT_ROOT / "docs" / "DECISIONS.md").read_text(encoding="utf-8")
        assert "## ADR-024." in text
        assert "yield return" in text and "ImmediateCaller" in text

    def test_changelog_has_entry(self) -> None:
        text = (PROJECT_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
        assert "## Хотфикс 0.6.5" in text
        assert "0.6.4.1" in text, "в переписке хотфикс зовётся 0.6.4.1 — связь обязана быть видна"
