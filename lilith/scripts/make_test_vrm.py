#!/usr/bin/env python3
"""Генератор тестовой VRM 1.0 (решение C5.3): процедурный «куб с лицом».

Зачем: настоящего тела Лилит ещё нет (Кирюша печатает его в VRoid Studio, C5.1),
а проверить пайплайн Unity-клиента нужно уже сейчас — своп персоны, виземы,
моргание, эмоции. Этот скрипт собирает **валидный VRM 1.0** (контейнер GLB +
расширение ``VRMC_vrm``) без единой внешней зависимости:

* полный humanoid-скелет в T-позе (52 кости) — чтобы ``RuntimeControlRig``
  UniVRM собрался и ``Animator.isHuman`` был true;
* два скиннутых меша (голова-куб и тело-«торс») с морф-таргетами;
* экспрессии VRM 1.0: ``aa ih ou ee oh`` (виземы), ``happy angry sad relaxed
  surprised`` (эмоции; ``neutral`` в VRM 1.0 — не пресет, а нулевые веса), ``blink`` (моргание);
* ``lookAt`` на костях, ``firstPerson.meshAnnotations: []`` (auto), ``springBone`` пустой;

Запуск::

    python scripts/make_test_vrm.py
    python scripts/make_test_vrm.py --out personas/lilith/model.vrm --name Lilith

Файл кладётся в ``tests/samples/test_cube.vrm`` (в репозитории: ~40 КБ, git это
переживёт — в отличие от настоящих тел на десятки мегабайт, D5.4).

Проверка после генерации: скрипт сам перечитывает контейнер и валидирует
структуру GLB/JSON, а ``tests/test_vrm_sample.py`` держит это под pytest.
"""

from __future__ import annotations

import argparse
import json
import math
import struct
from pathlib import Path
from typing import Any

#: Куда кладём результат по умолчанию (относительно корня проекта).
DEFAULT_OUT = "tests/samples/test_cube.vrm"

#: VRM 1.0 = glTF 2.0 + VRMC_vrm.
GLTF_MAGIC = 0x46546C67  # "glTF" little-endian
GLTF_VERSION = 2
CHUNK_JSON = 0x4E4F534A  # "JSON"
CHUNK_BIN = 0x004E4942   # "BIN\0"

#: Полный набор humanoid-костей VRM 1.0 (имена — как в VRMC_vrm.humanoid.humanBones).
HUMAN_BONES: tuple[str, ...] = (
    "hips", "spine", "chest", "upperChest", "neck", "head",
    "leftShoulder", "leftUpperArm", "leftLowerArm", "leftHand",
    "rightShoulder", "rightUpperArm", "rightLowerArm", "rightHand",
    "leftUpperLeg", "leftLowerLeg", "leftFoot", "leftToes",
    "rightUpperLeg", "rightLowerLeg", "rightFoot", "rightToes",
    "leftThumbMetacarpal", "leftThumbProximal", "leftThumbDistal",
    "leftIndexProximal", "leftIndexIntermediate", "leftIndexDistal",
    "leftMiddleProximal", "leftMiddleIntermediate", "leftMiddleDistal",
    "leftRingProximal", "leftRingIntermediate", "leftRingDistal",
    "leftLittleProximal", "leftLittleIntermediate", "leftLittleDistal",
    "rightThumbMetacarpal", "rightThumbProximal", "rightThumbDistal",
    "rightIndexProximal", "rightIndexIntermediate", "rightIndexDistal",
    "rightMiddleProximal", "rightMiddleIntermediate", "rightMiddleDistal",
    "rightRingProximal", "rightRingIntermediate", "rightRingDistal",
    "rightLittleProximal", "rightLittleIntermediate", "rightLittleDistal",
    "leftEye", "rightEye", "jaw",
)

#: Экспрессии VRM 1.0: имя → (тип пресета, морф-таргет, «нейтральный» вес).
EXPRESSIONS: tuple[tuple[str, str, int], ...] = (
    ("aa", "aa", 0),
    ("ih", "ih", 1),
    ("ou", "ou", 2),
    ("ee", "ee", 3),
    ("oh", "oh", 4),
    ("happy", "happy", 5),
    ("angry", "angry", 6),
    ("sad", "sad", 7),
    ("relaxed", "relaxed", 8),
    ("surprised", "surprised", 9),
    ("blink", "blink", 10),
)


# --------------------------------------------------------------------------- #
#  Геометрия
# --------------------------------------------------------------------------- #
def _box_vertices(cx: float, cy: float, cz: float, sx: float, sy: float, sz: float) -> tuple[list[float], list[float], list[int]]:
    """Куб с центром в (cx,cy,cz) и полуосями (sx,sy,sz): вершины, нормали, индексы."""
    positions: list[float] = []
    normals: list[float] = []
    indices: list[int] = []

    faces = (
        ((0, 0, 1), (-1, -1, 1), (1, -1, 1), (1, 1, 1), (-1, 1, 1)),
        ((0, 0, -1), (1, -1, -1), (-1, -1, -1), (-1, 1, -1), (1, 1, -1)),
        ((1, 0, 0), (1, -1, 1), (1, -1, -1), (1, 1, -1), (1, 1, 1)),
        ((-1, 0, 0), (-1, -1, -1), (-1, -1, 1), (-1, 1, 1), (-1, 1, -1)),
        ((0, 1, 0), (-1, 1, 1), (1, 1, 1), (1, 1, -1), (-1, 1, -1)),
        ((0, -1, 0), (-1, -1, -1), (1, -1, -1), (1, -1, 1), (-1, -1, 1)),
    )
    for normal, *corners in faces:
        base = len(positions) // 3
        for corner in corners:
            positions.extend((cx + corner[0] * sx, cy + corner[1] * sy, cz + corner[2] * sz))
            normals.extend(normal)
        indices.extend((base, base + 1, base + 2, base, base + 2, base + 3))
    return positions, normals, indices


def _morph_target(scale: float, axis: str = "y") -> list[float]:
    """Морф-таргет: смещения вершин (дельты), а не абсолютные позиции.

    Делаем «открытый рот» / «улыбку» простым раздуванием по оси — этого достаточно,
    чтобы UniVRM показал движение блендшейпа, а глаз Кирюши — что пайплайн живой.
    """
    positions, _normals, _indices = _box_vertices(0.0, 0.0, 0.0, 0.5, 0.5, 0.5)
    out: list[float] = []
    for i in range(0, len(positions), 3):
        dx = dy = dz = 0.0
        if axis == "y":
            dy = -scale if positions[i + 2] > 0 else 0.0
        elif axis == "x":
            dx = scale if positions[i + 2] > 0 else 0.0
        else:
            dz = scale if positions[i + 1] < 0 else 0.0
        out.extend((dx, dy, dz))
    return out


# --------------------------------------------------------------------------- #
#  Скелет
# --------------------------------------------------------------------------- #
def _bone_layout() -> dict[str, tuple[str | None, tuple[float, float, float]]]:
    """Карта кость → (родитель, локальная позиция). T-поза: руки вдоль ±X."""
    layout: dict[str, tuple[str | None, tuple[float, float, float]]] = {
        "hips": (None, (0.0, 0.95, 0.0)),
        "spine": ("hips", (0.0, 0.10, 0.0)),
        "chest": ("spine", (0.0, 0.12, 0.0)),
        "upperChest": ("chest", (0.0, 0.12, 0.0)),
        "neck": ("upperChest", (0.0, 0.14, 0.0)),
        "head": ("neck", (0.0, 0.08, 0.0)),
        "jaw": ("head", (0.0, 0.03, 0.02)),
        "leftEye": ("head", (0.032, 0.10, 0.05)),
        "rightEye": ("head", (-0.032, 0.10, 0.05)),
    }

    for side, sign in (("left", 1.0), ("right", -1.0)):
        layout[f"{side}Shoulder"] = ("upperChest", (sign * 0.04, 0.10, 0.0))
        layout[f"{side}UpperArm"] = (f"{side}Shoulder", (sign * 0.08, 0.0, 0.0))
        layout[f"{side}LowerArm"] = (f"{side}UpperArm", (sign * 0.25, 0.0, 0.0))
        layout[f"{side}Hand"] = (f"{side}LowerArm", (sign * 0.23, 0.0, 0.0))
        layout[f"{side}UpperLeg"] = ("hips", (sign * 0.09, -0.05, 0.0))
        layout[f"{side}LowerLeg"] = (f"{side}UpperLeg", (0.0, -0.42, 0.0))
        layout[f"{side}Foot"] = (f"{side}LowerLeg", (0.0, -0.40, 0.0))
        layout[f"{side}Toes"] = (f"{side}Foot", (0.0, 0.0, 0.10))

        hand = f"{side}Hand"
        for finger, spread in (("Thumb", 0.03), ("Index", 0.02), ("Middle", 0.0), ("Ring", -0.02), ("Little", -0.04)):
            base = f"{side}{finger}"
            first = "Metacarpal" if finger == "Thumb" else "Proximal"
            layout[f"{base}{first}"] = (hand, (sign * 0.05, spread, 0.02))
            layout[f"{base}Proximal" if finger == "Thumb" else f"{base}Intermediate"] = (
                f"{base}{first}",
                (sign * 0.04, 0.0, 0.0),
            )
            layout[f"{base}Distal"] = (
                f"{base}Proximal" if finger == "Thumb" else f"{base}Intermediate",
                (sign * 0.03, 0.0, 0.0),
            )
    return layout


def _mat4_translation(x: float, y: float, z: float) -> list[float]:
    """Матрица 4×4 переноса в столбцовом порядке (как ждёт glTF)."""
    return [
        1.0, 0.0, 0.0, 0.0,
        0.0, 1.0, 0.0, 0.0,
        0.0, 0.0, 1.0, 0.0,
        float(x), float(y), float(z), 1.0,
    ]


def _mat4_inverse_translation(x: float, y: float, z: float) -> list[float]:
    """Обратная матрица переноса (inverseBindMatrix для тождественного скина)."""
    return _mat4_translation(-x, -y, -z)


# --------------------------------------------------------------------------- #
#  Сборка GLB
# --------------------------------------------------------------------------- #
class _BufferBuilder:
    """Копит бинарные данные и выдаёт описатели accessor/bufferView."""

    def __init__(self) -> None:
        self.data = bytearray()
        self.views: list[dict[str, Any]] = []
        self.accessors: list[dict[str, Any]] = []

    @staticmethod
    def _pad4(data: bytearray) -> None:
        while len(data) % 4:
            data.append(0)

    def add(self, raw: bytes, target: int | None = None) -> int:
        """Добавить blob, вернуть индекс bufferView."""
        self._pad4(self.data)
        offset = len(self.data)
        self.data.extend(raw)
        view: dict[str, Any] = {"buffer": 0, "byteOffset": offset, "byteLength": len(raw)}
        if target is not None:
            view["target"] = target
        self.views.append(view)
        return len(self.views) - 1

    def add_accessor(
        self,
        raw: bytes,
        component_type: int,
        count: int,
        accessor_type: str,
        *,
        target: int | None = None,
        min_values: list[float] | None = None,
        max_values: list[float] | None = None,
    ) -> int:
        """Добавить bufferView + accessor, вернуть индекс accessor'а."""
        view_index = self.add(raw, target=target)
        accessor: dict[str, Any] = {
            "bufferView": view_index,
            "componentType": component_type,
            "count": count,
            "type": accessor_type,
        }
        if min_values is not None:
            accessor["min"] = min_values
        if max_values is not None:
            accessor["max"] = max_values
        self.accessors.append(accessor)
        return len(self.accessors) - 1


def _pack_floats(values: list[float]) -> bytes:
    """Список float32 → байты."""
    return struct.pack(f"<{len(values)}f", *values)


def _pack_ushorts(values: list[int]) -> bytes:
    """Список uint16 → байты."""
    return struct.pack(f"<{len(values)}H", *values)


def _pack_shorts(values: list[int]) -> bytes:
    """Список int16 → байты (скин-веса)."""
    return struct.pack(f"<{len(values)}h", *values)


def build_vrm(name: str = "TestCube", author: str = "LILITH-CORE") -> bytes:
    """Собрать байты валидного VRM 1.0 (glTF 2.0 + VRMC_vrm)."""
    layout = _bone_layout()
    buf = _BufferBuilder()

    # --- геометрия: голова-куб (11 морф-таргетов) и тело ------------------- #
    head_positions, head_normals, head_indices = _box_vertices(0.0, 1.62, 0.0, 0.13, 0.13, 0.13)
    head_vertex_count = len(head_positions) // 3

    pos_accessor = buf.add_accessor(
        _pack_floats(head_positions), 5126, head_vertex_count, "VEC3",
        target=34962,
        min_values=[min(head_positions[0::3]), min(head_positions[1::3]), min(head_positions[2::3])],
        max_values=[max(head_positions[0::3]), max(head_positions[1::3]), max(head_positions[2::3])],
    )
    normal_accessor = buf.add_accessor(_pack_floats(head_normals), 5126, head_vertex_count, "VEC3", target=34962)
    index_accessor = buf.add_accessor(_pack_ushorts(head_indices), 5123, len(head_indices), "SCALAR", target=34963)

    # Скин: все вершины привязаны к кости head (joint 0 → индекс кости head).
    #
    # Хотфикс 0.6.5: JOINTS_0 объявлен как VEC4, значит на вершину нужно ЧЕТЫРЕ
    # uint16, а не один. Раньше сюда писалось `[index] * count` — 48 байт вместо
    # 192, то есть accessor выходил за пределы своего bufferView. По спеке glTF
    # это нарушение, а UniVRM читал за границей вьюхи и получал мусорные индексы
    # костей (до 65535 при 55 суставах). Пара к WEIGHTS_0 = [1, 0, 0, 0]:
    # первый сустав держит весь вес, остальные три — нулевые.
    head_bone_index = HUMAN_BONES.index("head")
    joints_accessor = buf.add_accessor(
        _pack_ushorts([head_bone_index, 0, 0, 0] * head_vertex_count),
        5123,
        head_vertex_count,
        "VEC4",
        target=34962,
    )
    weights_accessor = buf.add_accessor(
        _pack_floats([1.0, 0.0, 0.0, 0.0] * head_vertex_count), 5126, head_vertex_count, "VEC4", target=34962
    )

    morph_targets: list[dict[str, Any]] = []
    for index, (_expr_name, _preset, _morph) in enumerate(EXPRESSIONS):
        axis = "y" if index % 3 == 0 else ("x" if index % 3 == 1 else "z")
        scale = 0.05 + 0.006 * index
        deltas = _morph_target(scale, axis)
        target_accessor = buf.add_accessor(_pack_floats(deltas), 5126, head_vertex_count, "VEC3", target=34962)
        morph_targets.append({"POSITION": target_accessor})

    body_positions, body_normals, body_indices = _box_vertices(0.0, 1.20, 0.0, 0.17, 0.30, 0.10)
    body_vertex_count = len(body_positions) // 3
    body_pos_accessor = buf.add_accessor(
        _pack_floats(body_positions), 5126, body_vertex_count, "VEC3",
        target=34962,
        min_values=[min(body_positions[0::3]), min(body_positions[1::3]), min(body_positions[2::3])],
        max_values=[max(body_positions[0::3]), max(body_positions[1::3]), max(body_positions[2::3])],
    )
    body_normal_accessor = buf.add_accessor(_pack_floats(body_normals), 5126, body_vertex_count, "VEC3", target=34962)
    body_index_accessor = buf.add_accessor(_pack_ushorts(body_indices), 5123, len(body_indices), "SCALAR", target=34963)

    hips_bone_index = HUMAN_BONES.index("hips")
    body_joints_accessor = buf.add_accessor(
        # 0.6.5: то же, что и у head — VEC4 означает 4 uint16 на вершину.
        _pack_ushorts([hips_bone_index, 0, 0, 0] * body_vertex_count),
        5123,
        body_vertex_count,
        "VEC4",
        target=34962,
    )
    body_weights_accessor = buf.add_accessor(
        _pack_floats([1.0, 0.0, 0.0, 0.0] * body_vertex_count), 5126, body_vertex_count, "VEC4", target=34962
    )

    # --- inverse bind matrices ---------------------------------------------- #
    ibm_values: list[float] = []
    for bone in HUMAN_BONES:
        world = _world_position(bone, layout)
        ibm_values.extend(_mat4_inverse_translation(*world))
    ibm_accessor = buf.add_accessor(_pack_floats(ibm_values), 5126, len(HUMAN_BONES), "MAT4")

    # --- узлы ---------------------------------------------------------------- #
    nodes: list[dict[str, Any]] = []
    bone_node_index: dict[str, int] = {}

    # Корневой узел VRM (его требуют экспортеры;UniVRM терпит и без него, но так честнее).
    nodes.append({"name": "VRMroot", "children": []})
    root_index = 0

    for bone in HUMAN_BONES:
        parent, translation = layout[bone]
        index = len(nodes)
        bone_node_index[bone] = index
        node: dict[str, Any] = {"name": f"J_{bone}", "translation": list(translation)}
        nodes.append(node)
        if parent is not None:
            nodes[bone_node_index[parent]].setdefault("children", []).append(index)
        else:
            nodes[root_index]["children"].append(index)

    # Меш-узлы: голова (скинненная, с морфами) и тело.
    head_node_index = len(nodes)
    nodes.append({"name": "FaceMesh", "mesh": 0, "skin": 0})
    nodes[root_index]["children"].append(head_node_index)
    body_node_index = len(nodes)
    nodes.append({"name": "BodyMesh", "mesh": 1, "skin": 0})
    nodes[root_index]["children"].append(body_node_index)

    # --- материал, меши, скин ------------------------------------------------- #
    material = {
        "name": "TestCubeSkin",
        "pbrMetallicRoughness": {
            "baseColorFactor": [0.95, 0.80, 0.86, 1.0],
            "metallicFactor": 0.0,
            "roughnessFactor": 0.7,
        },
        "doubleSided": False,
    }

    head_mesh = {
        "name": "Face",
        "primitives": [
            {
                "attributes": {
                    "POSITION": pos_accessor,
                    "NORMAL": normal_accessor,
                    "JOINTS_0": joints_accessor,
                    "WEIGHTS_0": weights_accessor,
                },
                "indices": index_accessor,
                "material": 0,
                "mode": 4,
                "targets": morph_targets,
            }
        ],
        "weights": [0.0] * len(EXPRESSIONS),
    }
    body_mesh = {
        "name": "Body",
        "primitives": [
            {
                "attributes": {
                    "POSITION": body_pos_accessor,
                    "NORMAL": body_normal_accessor,
                    "JOINTS_0": body_joints_accessor,
                    "WEIGHTS_0": body_weights_accessor,
                },
                "indices": body_index_accessor,
                "material": 0,
                "mode": 4,
            }
        ],
    }

    skin = {"name": "TestCubeSkin", "inverseBindMatrices": ibm_accessor, "joints": [bone_node_index[b] for b in HUMAN_BONES]}

    # --- VRMC_vrm --------------------------------------------------------------- #
    human_bones = {
        bone: {"node": bone_node_index[bone]} for bone in HUMAN_BONES
    }
    expressions: dict[str, Any] = {"preset": {}, "custom": {}}
    for expr_name, preset, morph_index in EXPRESSIONS:
        expressions["preset"][preset] = {
            "morphTargetBindings": [
                {"node": head_node_index, "mesh": 0, "index": morph_index, "weight": 1.0}
            ],
            "isBinary": False,
            "overrideBlink": "none",
            "overrideLookAt": "none",
            "overrideMouth": "none",
        }

    vrm_extension = {
        "specVersion": "1.0",
        "meta": {
            "name": name,
            "version": "1.0.0",
            "authors": [author],
            "copyrightInformation": f"{author} · процедурная тестовая модель",
            "licenseUrl": "https://vrm.dev/licenses/1.0/",
            "allowExcessivelyViolentUsage": True,
            "allowExcessivelySexualUsage": False,
            "allowPoliticalOrReligiousUsage": False,
            "allowAntisocialOrHateUsage": False,
            "avatarPermission": "onlyAuthor",
            # 0.6.6: было "personal" — такого значения в VRM 1.0 НЕТ
            # (спека: personalNonProfit | personalProfit | corporation). Полевая
            # правка Кирюши подтверждена спецификацией; гвард — validate() ниже.
            "commercialUsage": "personalNonProfit",
            "creditNotation": "required",
            "allowRedistribution": False,
            "modification": "prohibited",
            "otherLicenseUrl": "",
        },
        "humanoid": {"humanBones": human_bones},
        "expressions": expressions,
        "lookAt": {
            "type": "bone",
            "offsetFromHeadBone": [0.0, 0.0, 0.0],
            "range": {
                "inputMaxValueDegrees": {"horizontalInner": 90.0, "horizontalOuter": 90.0, "verticalDown": 90.0, "verticalUp": 90.0},
                "outputScaleDegrees": {"horizontalInner": 10.0, "horizontalOuter": 10.0, "verticalDown": 10.0, "verticalUp": 10.0},
            },
        },
        "firstPerson": {"meshAnnotations": []},
        "springBone": {"colliders": [], "skeletons": []},
    }
    gltf: dict[str, Any] = {
        "asset": {"version": "2.0", "generator": "LILITH-CORE make_test_vrm.py"},
        "scene": 0,
        "scenes": [{"name": name, "nodes": [root_index]}],
        "nodes": nodes,
        "meshes": [head_mesh, body_mesh],
        "materials": [material],
        "skins": [skin],
        "accessors": buf.accessors,
        "bufferViews": buf.views,
        "buffers": [{"byteLength": len(buf.data)}],
        "extensionsUsed": ["VRMC_vrm"],
        "extensions": {"VRMC_vrm": vrm_extension},
    }

    json_bytes = json.dumps(gltf, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    while len(json_bytes) % 4:
        json_bytes += b" "

    binary = bytes(buf.data)
    while len(binary) % 4:
        binary += b"\x00"

    total_length = 12 + 8 + len(json_bytes) + 8 + len(binary)
    out = bytearray()
    out.extend(struct.pack("<III", GLTF_MAGIC, GLTF_VERSION, total_length))
    out.extend(struct.pack("<II", len(json_bytes), CHUNK_JSON))
    out.extend(json_bytes)
    out.extend(struct.pack("<II", len(binary), CHUNK_BIN))
    out.extend(binary)
    return bytes(out)


def _world_position(bone: str, layout: dict[str, tuple[str | None, tuple[float, float, float]]]) -> tuple[float, float, float]:
    """Мировая позиция кости (сумма локальных смещений вверх по иерархии)."""
    x = y = z = 0.0
    current: str | None = bone
    guard = 0
    while current is not None and guard < 64:
        parent, translation = layout[current]
        x += translation[0]
        y += translation[1]
        z += translation[2]
        current = parent
        guard += 1
    return x, y, z


#: Размер компоненты glTF по componentType (спека glTF 2.0, таблица accessor).
_COMPONENT_SIZE = {5120: 1, 5121: 1, 5122: 2, 5123: 2, 5125: 4, 5126: 4}

#: Число компонент по типу accessor'а.
_COMPONENT_COUNT = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT2": 4, "MAT3": 9, "MAT4": 16}


def _validate_buffers(gltf: dict[str, Any], binary_length: int) -> None:
    """Проверить геометрию bufferView/accessor (**хотфикс 0.6.5**).

    Ровно та проверка, которой не хватало с этапа 6: генератор написал JOINTS_0
    одним uint16 на вершину при объявленном VEC4, accessor вышел за границы
    bufferView, и ``validate()`` этого не заметил — модель уехала в репо и в архив.
    UniVRM на таких байтах читает за пределами вьюхи. Теперь любая подобная
    рассинхронизация ловится до сохранения файла.
    """
    views = gltf.get("bufferViews", [])
    accessors = gltf.get("accessors", [])
    buffer_length = int(gltf["buffers"][0].get("byteLength", 0))
    if buffer_length != binary_length:
        raise ValueError(f"buffers[0].byteLength={buffer_length} != BIN-чанк {binary_length}")

    for index, view in enumerate(views):
        start = int(view.get("byteOffset", 0))
        end = start + int(view["byteLength"])
        if int(view["byteLength"]) <= 0:
            raise ValueError(f"bufferView[{index}] нулевой длины")
        if end > binary_length:
            raise ValueError(f"bufferView[{index}] выходит за BIN-чанк: {end} > {binary_length}")

    for index, accessor in enumerate(accessors):
        component_type = int(accessor["componentType"])
        accessor_type = accessor["type"]
        if component_type not in _COMPONENT_SIZE:
            raise ValueError(f"accessor[{index}]: неизвестный componentType {component_type}")
        if accessor_type not in _COMPONENT_COUNT:
            raise ValueError(f"accessor[{index}]: неизвестный type {accessor_type}")
        need = _COMPONENT_SIZE[component_type] * _COMPONENT_COUNT[accessor_type] * int(accessor["count"])
        view = views[int(accessor["bufferView"])]
        offset = int(accessor.get("byteOffset", 0))
        if offset + need > int(view["byteLength"]):
            raise ValueError(
                f"accessor[{index}] ({accessor_type}, componentType {component_type}, "
                f"count {accessor['count']}) требует {offset + need} Б, "
                f"а bufferView[{accessor['bufferView']}] вмещает {view['byteLength']} Б"
            )

    # Вершины одного примитива обязаны быть согласованы по count.
    for mesh_index, mesh in enumerate(gltf.get("meshes", [])):
        for prim_index, primitive in enumerate(mesh.get("primitives", [])):
            counts = {}
            for attribute, accessor_index in primitive.get("attributes", {}).items():
                counts[attribute] = int(accessors[int(accessor_index)]["count"])
            if len(set(counts.values())) > 1:
                raise ValueError(f"mesh[{mesh_index}].primitive[{prim_index}]: атрибуты с разным count {counts}")
            for target_index, target in enumerate(primitive.get("targets", [])):
                for attribute, accessor_index in target.items():
                    target_count = int(accessors[int(accessor_index)]["count"])
                    if counts and target_count not in counts.values():
                        raise ValueError(
                            f"mesh[{mesh_index}].primitive[{prim_index}].targets[{target_index}]."
                            f"{attribute}: count {target_count} != count вершин {counts}"
                        )


#: Допустимые значения enum-полей ``VRMC_vrm.meta`` по спецификации VRM 1.0
#: (vrm-c/vrm-specification, specification/VRMC_vrm-1.0/meta.md, сверено 2026-09-22).
#: Гвард 0.6.6: наш куб до правки писал ``commercialUsage: "personal"`` — значения
#: нет в спеке, UniVRM такое либо отвергает, либо подставляет дефолт молча.
META_ENUMS: dict[str, set[str]] = {
    "avatarPermission": {"onlyAuthor", "onlySeparatelyLicensedPerson", "everyone"},
    "commercialUsage": {"personalNonProfit", "personalProfit", "corporation"},
    "creditNotation": {"required", "unnecessary"},
    "modification": {"prohibited", "allowModification", "allowModificationRedistribution"},
}

#: Булевы поля меты (по спеке) — гвард против "yes"/"true"-строк.
META_BOOLEANS: tuple[str, ...] = (
    "allowExcessivelyViolentUsage",
    "allowExcessivelySexualUsage",
    "allowPoliticalOrReligiousUsage",
    "allowAntisocialOrHateUsage",
    "allowRedistribution",
)


def validate_meta(meta: dict[str, Any]) -> None:
    """Проверить ``VRMC_vrm.meta`` против спецификации VRM 1.0.

    Поднимает ``ValueError`` на любом отклонении: обязательные поля, enum-значения,
    типы булевых. Это контрольный выстрел для полевой правки 0.6.6: куб со
    значением ``"personal"`` обязан упасть здесь, а не молча доехать до Unity.
    """
    if not isinstance(meta, dict):
        raise ValueError("meta — не объект")

    name = meta.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("meta.name обязателен и не может быть пустым (спека VRM 1.0)")

    authors = meta.get("authors")
    if not isinstance(authors, list) or not any(isinstance(a, str) and a.strip() for a in authors):
        raise ValueError("meta.authors обязан содержать хотя бы одну непустую строку (спека VRM 1.0)")

    license_url = meta.get("licenseUrl")
    if not isinstance(license_url, str) or not license_url.strip():
        raise ValueError("meta.licenseUrl обязателен (спека VRM 1.0)")

    for field, allowed in META_ENUMS.items():
        if field not in meta:
            continue  # поле опционально, у него есть дефолт по спеке
        value = meta[field]
        if value not in allowed:
            raise ValueError(
                f"meta.{field}={value!r} не входит в спецификацию VRM 1.0: "
                f"допустимы {sorted(allowed)}"
            )

    for field in META_BOOLEANS:
        if field in meta and not isinstance(meta[field], bool):
            raise ValueError(f"meta.{field} обязан быть boolean, получено {type(meta[field]).__name__}")


def validate(data: bytes) -> dict[str, Any]:
    """Перечитать собранный GLB и проверить структуру (без внешних библиотек)."""
    if len(data) < 20:
        raise ValueError("файл слишком короткий")
    magic, version, length = struct.unpack("<III", data[:12])
    if magic != GLTF_MAGIC:
        raise ValueError(f"это не GLB: magic={magic:#x}")
    if version != GLTF_VERSION:
        raise ValueError(f"ожидался glTF 2.0, получено {version}")
    if length != len(data):
        raise ValueError(f"в заголовке длина {length}, в файле {len(data)}")

    json_length, json_type = struct.unpack("<II", data[12:20])
    if json_type != CHUNK_JSON:
        raise ValueError("первый чанк — не JSON")
    payload = data[20 : 20 + json_length]
    gltf = json.loads(payload.decode("utf-8"))

    binary_offset = 20 + json_length
    if binary_offset + 8 > len(data):
        raise ValueError("в GLB нет BIN-чанка")
    binary_length, binary_type = struct.unpack("<II", data[binary_offset : binary_offset + 8])
    if binary_type != CHUNK_BIN:
        raise ValueError("второй чанк — не BIN")
    if binary_offset + 8 + binary_length > len(data):
        raise ValueError("BIN-чанк выходит за пределы файла")

    _validate_buffers(gltf, binary_length)

    vrm = gltf.get("extensions", {}).get("VRMC_vrm")
    if vrm is None:
        raise ValueError("нет расширения VRMC_vrm — это не VRM")
    if vrm.get("specVersion") != "1.0":
        raise ValueError(f"specVersion != 1.0: {vrm.get('specVersion')}")

    meta = vrm.get("meta")
    if meta is None:
        raise ValueError("нет VRMC_vrm.meta")
    validate_meta(meta)

    presets = set(vrm["expressions"]["preset"])
    # NB: в VRM 1.0 пресета "neutral" нет — нейтральное лицо это все веса по нулям.
    required = {"aa", "ih", "ou", "ee", "oh", "happy", "angry", "sad", "relaxed", "surprised", "blink"}
    missing = required - presets
    if missing:
        raise ValueError(f"не хватает экспрессий: {sorted(missing)}")

    return {
        "bytes": len(data),
        "nodes": len(gltf["nodes"]),
        "meshes": len(gltf["meshes"]),
        "human_bones": len(vrm["humanoid"]["humanBones"]),
        "expressions": sorted(presets),
        "morph_targets": len(gltf["meshes"][0]["primitives"][0].get("targets", [])),
        "binary_bytes": binary_length,
        "commercial_usage": meta.get("commercialUsage"),
    }


def main() -> None:
    """Точка входа CLI."""
    parser = argparse.ArgumentParser(description="Собрать тестовую VRM 1.0 (процедурный куб с лицом).")
    parser.add_argument("--out", default=DEFAULT_OUT, help=f"куда сохранить (по умолчанию {DEFAULT_OUT})")
    parser.add_argument("--name", default="TestCube", help="имя модели в meta.name")
    parser.add_argument("--author", default="LILITH-CORE", help="автор в meta.authors")
    args = parser.parse_args()

    project_root = Path(__file__).resolve().parents[1]
    out_path = Path(args.out)
    if not out_path.is_absolute():
        out_path = project_root / out_path

    data = build_vrm(name=args.name, author=args.author)
    info = validate(data)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(data)

    print(f"VRM 1.0 собран: {out_path}")
    for key, value in info.items():
        print(f"  {key:>14}: {value}")
    print("\nКуда положить, чтобы сервер и Unity его увидели:")
    print("  personas/<id>/face.yaml →  vrm_path: \"<путь к этому файлу>\"")
    print("  (в репозиторий настоящие тела не кладём — решение D5.4)")


if __name__ == "__main__":
    main()
