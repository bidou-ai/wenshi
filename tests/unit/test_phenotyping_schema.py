from pathlib import Path

import yaml


PLANT_IDS = [
    *(f"A-{index:02d}" for index in range(1, 9)),
    *(f"B-L-{index:02d}" for index in range(1, 9)),
    *(f"B-R-{index:02d}" for index in range(1, 9)),
    *(f"C-{index:02d}" for index in range(1, 9)),
]


def _placeholder_config():
    return {
        "phenotyping": {
            "enabled": False,
            "traits": ["plant_height", "effective_panicle_count"],
            "processing_mode": "offline",
            "capture_burst_count": 3,
            "capture_burst_max": 5,
            "max_retries_per_view": 3,
        },
        "april_tag": {
            "family": "tag25h7",
            "physical_size_m": None,
            "detector_backend": None,
            "mounting_orientation": None,
        },
        "plants": [
            {
                "plant_id": plant_id,
                "tag_id": None,
                "region": plant_id.split("-")[0],
                "row": "row-1",
                "index": int(plant_id.rsplit("-", 1)[-1]),
                "observation_group": (
                    f"left-{int(plant_id.rsplit('-', 1)[-1]):02d}"
                    if plant_id.startswith(("A-", "B-L-"))
                    else f"right-{int(plant_id.rsplit('-', 1)[-1]):02d}"
                ),
                "camera_side": "left" if plant_id.startswith(("A-", "B-L-")) else "right",
                "slot_top_to_water_m": None,
            }
            for plant_id in PLANT_IDS
        ],
        "observation_groups": [
            {
                "id": f"left-{index:02d}",
                "route_segment": None,
                "approximate_along_track_m": None,
                "slowdown_before_m": None,
                "trigger_distance_m": None,
                "left_plant_id": f"A-{index:02d}",
                "right_plant_id": f"B-L-{index:02d}",
            }
            for index in range(1, 9)
        ]
        + [
            {
                "id": f"right-{index:02d}",
                "route_segment": None,
                "approximate_along_track_m": None,
                "slowdown_before_m": None,
                "trigger_distance_m": None,
                "left_plant_id": f"B-R-{index:02d}",
                "right_plant_id": f"C-{index:02d}",
            }
            for index in range(1, 9)
        ],
    }


def test_placeholder_configuration_loads_and_describes_formal_gate():
    from wenshi_patrol.phenotyping.config import load_phenotyping_config

    loaded = load_phenotyping_config(_placeholder_config())

    assert len(loaded.plants) == 32
    assert len(loaded.observation_groups) == 16
    assert loaded.formal_ready is False
    assert loaded.validation_errors


def test_loaded_config_has_32_plants_and_16_groups():
    from wenshi_patrol.phenotyping.config import load_phenotyping_config

    value = yaml.safe_load(
        Path(__file__).parents[2].joinpath("config", "wenshi.yaml").read_text(encoding="utf-8")
    )
    loaded = load_phenotyping_config(value)

    assert len(loaded.plants) == 32
    assert len(loaded.observation_groups) == 16


def test_validation_requires_32_unique_plant_ids_and_two_plants_per_group():
    from wenshi_patrol.phenotyping.config import validate_phenotyping_config

    value = _placeholder_config()
    value["plants"][1]["plant_id"] = value["plants"][0]["plant_id"]
    value["observation_groups"][0]["right_plant_id"] = None

    errors = validate_phenotyping_config(value)

    assert any("唯一" in error or "重复" in error for error in errors)
    assert any("两个" in error or "two" in error.lower() for error in errors)


def test_validation_rejects_duplicate_tag_ids_when_present():
    from wenshi_patrol.phenotyping.config import validate_phenotyping_config

    value = _placeholder_config()
    value["plants"][0]["tag_id"] = 7
    value["plants"][1]["tag_id"] = 7

    errors = validate_phenotyping_config(value)

    assert any("Tag" in error and ("重复" in error or "duplicate" in error.lower()) for error in errors)


def test_validation_rejects_incomplete_position_definition_when_formal_enabled():
    from wenshi_patrol.phenotyping.config import validate_phenotyping_config

    value = _placeholder_config()
    value["phenotyping"]["enabled"] = True
    value["observation_groups"][0]["route_segment"] = "LM1"

    errors = validate_phenotyping_config(value)

    assert any("停车" in error or "position" in error.lower() for error in errors)


def test_new_plant_record_contains_reviewable_atomic_schema_fields():
    from wenshi_patrol.phenotyping.schema import PlantSpec, new_plant_record

    plant = PlantSpec(
        plant_id="A-01",
        tag_id=None,
        region="A",
        row="row-1",
        index=1,
        observation_group="left-01",
        camera_side="left",
        slot_top_to_water_m=None,
    )

    record = new_plant_record(plant, "run_20260828_120000")

    assert record["run_id"] == "run_20260828_120000"
    assert record["plant_id"] == "A-01"
    assert record["captures"] == {"left": None, "center": None, "right": None}
    assert record["traits"]["plant_height"]["auto_value_m"] is None
    assert record["traits"]["effective_panicle_count"]["reviewed_value"] is None
    assert record["review"]["status"] == "pending"


def test_loaded_config_preserves_unconfirmed_tag_size_mounting_and_compensation():
    from wenshi_patrol.phenotyping.config import load_phenotyping_config

    loaded = load_phenotyping_config(_placeholder_config())

    assert loaded.april_tag.physical_size_m is None
    assert loaded.april_tag.mounting_orientation is None
    assert loaded.plants[0].slot_top_to_water_m is None
    assert loaded.observation_groups[0].approximate_along_track_m is None


def _formal_config():
    value = _placeholder_config()
    value["phenotyping"].update(
        {
            "enabled": True,
            "arm_postures": {
                "left": "phenotype_left",
                "center": "phenotype_center",
                "right": "phenotype_right",
            },
            "camera_calibration": {"calibrated": True, "evidence": "calibration-20260828"},
            "water_compensation": {"calibrated": True, "evidence": "water-level-20260828"},
        }
    )
    value["april_tag"].update(
        {
            "physical_size_m": 0.08,
            "detector_backend": "pupil_apriltags",
            "mounting_orientation": "upward",
        }
    )
    for tag_id, plant in enumerate(value["plants"]):
        plant["tag_id"] = tag_id
        plant["slot_top_to_water_m"] = 0.12
    for group in value["observation_groups"]:
        group.update(
            {
                "route_segment": "LM1",
                "approximate_along_track_m": 1.0,
                "slowdown_before_m": 0.3,
                "trigger_distance_m": 0.2,
            }
        )
    return value


def test_validation_rejects_nonfinite_and_out_of_range_measurement_values():
    from wenshi_patrol.phenotyping.config import validate_phenotyping_config

    value = _formal_config()
    value["april_tag"]["physical_size_m"] = "unknown"
    value["plants"][0]["slot_top_to_water_m"] = float("nan")
    value["observation_groups"][0]["approximate_along_track_m"] = -1.0
    value["observation_groups"][0]["trigger_distance_m"] = float("inf")

    errors = validate_phenotyping_config(value)

    assert any("尺寸" in error for error in errors)
    assert any("水面高度" in error for error in errors)
    assert any("路线距离" in error or "触发距离" in error for error in errors)


def test_validation_requires_tag25h7_and_exactly_once_group_coverage():
    from wenshi_patrol.phenotyping.config import validate_phenotyping_config

    value = _formal_config()
    value["april_tag"]["family"] = "tag36h11"
    value["observation_groups"][1]["left_plant_id"] = "A-01"

    errors = validate_phenotyping_config(value)

    assert any("tag25h7" in error for error in errors)
    assert any("恰好一次" in error for error in errors)


def test_formal_ready_requires_arm_postures_camera_calibration_and_water_evidence():
    from wenshi_patrol.phenotyping.config import load_phenotyping_config

    value = _formal_config()
    value["phenotyping"]["arm_postures"]["right"] = None
    value["phenotyping"]["camera_calibration"]["evidence"] = None
    value["phenotyping"]["water_compensation"]["calibrated"] = False

    loaded = load_phenotyping_config(value)

    assert loaded.formal_ready is False
    assert any("机械臂" in error for error in loaded.validation_errors)
    assert any("相机标定" in error for error in loaded.validation_errors)
    assert any("水位补偿" in error for error in loaded.validation_errors)


def test_invalid_capture_integers_produce_diagnostics_without_crashing_loader():
    from wenshi_patrol.phenotyping.config import load_phenotyping_config

    value = _placeholder_config()
    value["phenotyping"].update(
        {
            "capture_burst_count": "pending",
            "capture_burst_max": 1.5,
            "max_retries_per_view": None,
        }
    )

    loaded = load_phenotyping_config(value)

    assert loaded.capture_burst_count == 3
    assert loaded.capture_burst_max == 5
    assert loaded.max_retries_per_view == 3
    assert any("capture_burst_count" in error for error in loaded.validation_errors)
    assert any("capture_burst_max" in error for error in loaded.validation_errors)
    assert any("max_retries_per_view" in error for error in loaded.validation_errors)
