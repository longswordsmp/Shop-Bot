"""Feature readiness: a feature is shown to customers only when it is enabled AND fully configured.

Readiness is computed from settings and content at startup and after every configuration change. Panels
of a feature that is not ready are never published, and its controls answer with the configured
"unavailable" message (or are silently acknowledged if even that is missing).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from shopbot.config.content import ContentStore
from shopbot.config.content_keys import Registry
from shopbot.config.settings import Settings
from shopbot.features.base import FeatureModule
from shopbot.features.core_keys import CORE


@dataclass(frozen=True)
class Problem:
    feature: str
    kind: str  # missing_content | invalid_content | settings | dependency
    message: str
    key: str | None = None


@dataclass
class FeatureStatus:
    name: str
    enabled: bool
    ready: bool
    problems: list[Problem] = field(default_factory=list)


@dataclass
class Readiness:
    statuses: dict[str, FeatureStatus]
    unknown_content_keys: list[str] = field(default_factory=list)

    def is_ready(self, feature: str) -> bool:
        status = self.statuses.get(feature)
        return bool(status and status.ready)

    def is_enabled(self, feature: str) -> bool:
        status = self.statuses.get(feature)
        return bool(status and status.enabled)

    def problems(self, *, enabled_only: bool = False) -> list[Problem]:
        out: list[Problem] = []
        for status in self.statuses.values():
            if enabled_only and not status.enabled:
                continue
            out.extend(status.problems)
        return out


def build_registry(settings: Settings, modules: Sequence[FeatureModule]) -> Registry:
    registry = Registry()
    for module in modules:
        registry.add(*module.content_keys(settings))
    collisions = registry.check_no_prefix_collisions()
    if collisions:
        raise ValueError(f"content key prefix collisions: {collisions}")
    return registry


def compute_readiness(settings: Settings, content: ContentStore, modules: Sequence[FeatureModule]) -> Readiness:
    invalid: dict[str, list[str]] = {}
    for vp in content.value_problems():
        invalid.setdefault(vp.key, []).append(vp.message)

    customer_feature_enabled = any(m.customer_facing and m.is_enabled(settings) for m in modules)
    statuses: dict[str, FeatureStatus] = {}

    for module in modules:
        enabled = customer_feature_enabled if module.name == CORE else module.is_enabled(settings)
        problems: list[Problem] = []
        for message in module.settings_problems(settings):
            problems.append(Problem(module.name, "settings", message))
        for definition in content.registry.for_feature(module.name):
            if definition.required and not content.has(definition.key):
                problems.append(Problem(module.name, "missing_content", "not configured", definition.key))
            for message in invalid.get(definition.key, []):
                problems.append(Problem(module.name, "invalid_content", message, definition.key))
        for key in module.shared_keys(settings):
            if not content.has(key):
                problems.append(Problem(module.name, "missing_content", "not configured (shared key)", key))
        dependencies = list(module.depends_on)
        if module.customer_facing and module.name != CORE:
            dependencies.insert(0, CORE)
        for dependency in dependencies:
            dep_status = statuses.get(dependency)
            if dep_status is None or not dep_status.enabled:
                problems.append(Problem(module.name, "dependency", f"requires the '{dependency}' feature"))
            elif not dep_status.ready:
                problems.append(Problem(module.name, "dependency", f"requires the '{dependency}' feature to be ready"))
        statuses[module.name] = FeatureStatus(
            name=module.name, enabled=enabled, ready=enabled and not problems, problems=problems
        )

    return Readiness(statuses=statuses, unknown_content_keys=content.unknown_keys())
