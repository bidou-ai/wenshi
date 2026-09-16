"""Safety-first orchestration for arm-only and AGV full-route runs."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from .algorithms import AlgorithmParams, analyze_view, fuse_views
from .capture import ModelSuite, HttpRgbdSource, select_best_frame
from .models import HeightTestConfig, PlantHeightResult, TagObservation


@dataclass(frozen=True)
class RunOutcome:
    ok: bool
    plants: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()
    events: tuple[str, ...] = ()
    results: Mapping[str, PlantHeightResult] = field(default_factory=dict)


class HeightTestRunner:
    def __init__(self, config: HeightTestConfig, source: Any, models: ModelSuite, store: Any, arm: Any | None = None, agv: Any | None = None, setup: Any | None = None, input_stream: Any | None = None):
        self.config, self.source, self.models, self.store = config, source, models, store
        self.arm, self.agv, self.setup, self.input_stream = arm, agv, setup, input_stream
        self._stopped = False
        self._events: list[str] = []

    def _event(self, text: str, **values: Any) -> None:
        self._events.append(text)
        append = getattr(self.store, "append_event", None)
        if callable(append):
            append(text, **values)

    def _agv_stopped(self) -> bool:
        if self.agv is None:
            return True
        status = self.agv.get_status() if callable(getattr(self.agv, "get_status", None)) else getattr(self.agv, "status", {})
        if not isinstance(status, Mapping):
            return False
        age = status.get("status_age")
        if age is not None and float(age) > 1.0:
            return False
        return bool(status.get("is_stop", status.get("stopped", False)))

    def _move_arm(self, view: str) -> None:
        if self.arm is None:
            return
        if not self._agv_stopped():
            raise RuntimeError("AGV must be stopped before arm motion")
        for name in ("move_to_view", "move_view", "goto_view"):
            method = getattr(self.arm, name, None)
            if callable(method):
                result = method(view)
                if result is False:
                    raise RuntimeError(f"arm failed to move to {view}")
                return
        raise RuntimeError("arm adapter has no view movement method")

    def _safe_arm(self) -> None:
        if self.arm is None:
            return
        for name in ("move_to_safe", "move_home", "home"):
            method = getattr(self.arm, name, None)
            if callable(method):
                method(); return

    def _plant_tag(self, plant_id: str) -> TagObservation | None:
        setup = self.setup
        if setup is None:
            return None
        record = setup.tags.get(plant_id) if hasattr(setup, "tags") else setup.get("plants", {}).get(plant_id, {}) if isinstance(setup, Mapping) else None
        if not isinstance(record, Mapping):
            return None
        value = record.get("detection", record)
        if not isinstance(value, Mapping) or value.get("tag_id") is None:
            return None
        return TagObservation(tag_id=int(value["tag_id"]), corners=tuple(tuple(item) for item in value.get("corners", ())), score=value.get("score"), rvec=tuple(value["rvec"]) if value.get("rvec") else None, tvec=tuple(value["tvec"]) if value.get("tvec") else None, pose_valid=bool(value.get("pose_valid", False)), family=str(value.get("family", self.config.tag_family)))

    def _water_offset(self, plant_id: str) -> float | None:
        if hasattr(self.setup, "water_offsets"):
            return self.setup.water_offsets.get(plant_id)
        if isinstance(self.setup, Mapping):
            return self.setup.get("plants", {}).get(plant_id, {}).get("water_offset_m")
        return next((plant.slot_top_to_water_m for plant in self.config.plants if plant.plant_id == plant_id), None)

    def _capture_group(self, group_id: str) -> tuple[list[str], dict[str, PlantHeightResult], list[str]]:
        plants = list(self.config.group_plant_ids(group_id))
        results: dict[str, PlantHeightResult] = {}
        errors: list[str] = []
        for plant_id in plants:
            views = []
            for view_name in ("left", "center", "right"):
                self._move_arm(view_name)
                frames = self.source.capture_burst(int(self.config.quality.get("burst_count", 3))) if callable(getattr(self.source, "capture_burst", None)) else [self.source.capture()]
                frame = select_best_frame(frames, dict(self.config.quality))
                bundle = self.models.infer(frame.color)
                plant_box = bundle.plant[0] if bundle.plant else None
                analysis = analyze_view(frame, plant_box, bundle.panicle, self._plant_tag(plant_id), self._water_offset(plant_id), params=AlgorithmParams(depth_scale=float(self.config.quality.get("depth_scale", 0.001)), view_disagreement_m=float(self.config.quality.get("view_disagreement_m", 0.08))) )
                self.store.save_view(group_id, plant_id, view_name, frame, bundle, analysis)
                views.append(analysis)
            result = fuse_views(views, AlgorithmParams(view_disagreement_m=float(self.config.quality.get("view_disagreement_m", 0.08))), plant_id=plant_id)
            self.store.write_result(plant_id, result)
            results[plant_id] = result
            if result.quality != "ok":
                errors.append(f"{plant_id}: " + ",".join(result.reasons))
        self._safe_arm()
        return plants, results, errors

    def _setup_ready(self) -> bool:
        if self.setup is None:
            return False
        stations = self.setup.stations if hasattr(self.setup, "stations") else self.setup.get("stations", {}) if isinstance(self.setup, Mapping) else {}
        return set(stations) == set(self.config.groups)

    def run_arm_only(self, group_id: str) -> RunOutcome:
        if group_id not in self.config.groups:
            return RunOutcome(False, errors=(f"unknown group: {group_id}",))
        if not self._setup_ready():
            return RunOutcome(False, errors=("published setup is required",))
        if not self._agv_stopped():
            return RunOutcome(False, errors=("AGV is not stopped",))
        try:
            plants, results, errors = self._capture_group(group_id)
            return RunOutcome(not errors, tuple(plants), tuple(errors), tuple(self._events), results)
        except Exception as exc:
            self.stop(f"arm-only failure: {exc}")
            return RunOutcome(False, errors=(str(exc),), events=tuple(self._events))

    def _move_agv(self, station: Mapping[str, Any]) -> None:
        if self.agv is None:
            return
        pose = station.get("pose", station)
        for name in ("move_to_station", "go_to_station", "navigate_to", "move_to"):
            method = getattr(self.agv, name, None)
            if callable(method):
                result = method(pose)
                if result is False:
                    raise RuntimeError("AGV failed to reach station")
                return
        raise RuntimeError("AGV adapter has no station movement method")

    def run_full_route(self) -> RunOutcome:
        if not self._setup_ready():
            return RunOutcome(False, errors=("published setup is required",))
        all_plants: list[str] = []; results: dict[str, PlantHeightResult] = {}; errors: list[str] = []
        stations = self.setup.stations if hasattr(self.setup, "stations") else self.setup.get("stations", {})
        order = self.config.motion.get("station_order", tuple(self.config.groups))
        try:
            for group_id in order:
                station = stations[group_id]
                self._move_agv(station)
                if not self._agv_stopped():
                    raise RuntimeError(f"AGV did not settle at {group_id}")
                plants, group_results, group_errors = self._capture_group(group_id)
                all_plants.extend(plants); results.update(group_results); errors.extend(group_errors)
            return RunOutcome(not errors, tuple(all_plants), tuple(errors), tuple(self._events), results)
        except Exception as exc:
            self.stop(f"full-route failure: {exc}")
            return RunOutcome(False, tuple(all_plants), tuple((*errors, str(exc))), tuple(self._events), results)

    def stop(self, reason: str = "operator") -> None:
        if self._stopped:
            return
        self._stopped = True
        self._event("stop", reason=reason)
        for target in (self.arm, self.agv):
            for name in ("stop", "stop_motion", "emergency_stop"):
                method = getattr(target, name, None) if target is not None else None
                if callable(method):
                    try: method()
                    except Exception: pass
                    break


__all__ = ["HeightTestRunner", "RunOutcome"]
