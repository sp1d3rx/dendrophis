"""Dynamic tool discovery and dependency injection logic."""

from __future__ import annotations

import importlib
import inspect
import logging
import pkgutil
from typing import Any

from dendrophis.tools.base import BaseTool

logger = logging.getLogger(__name__)


def discover_tool_classes(package_names: list[str]) -> list[type[BaseTool]]:
    """Walk through package directories and find all subclasses of BaseTool."""
    discovered_classes: list[type[BaseTool]] = []

    for package_name in package_names:
        try:
            package = importlib.import_module(package_name)
        except ImportError as exception:
            logger.debug("Failed to import tool package %s: %s", package_name, exception)
            continue

        package_path = getattr(package, "__path__", None)
        if not package_path:
            _scan_module(package, package_name, discovered_classes)
            continue

        for _, module_name, _is_package in pkgutil.walk_packages(package_path, package_name + "."):
            try:
                imported_module = importlib.import_module(module_name)
                _scan_module(imported_module, package_name, discovered_classes)
            except ImportError as exception:
                logger.debug("Failed to import tool module %s: %s", module_name, exception)
                continue

    return discovered_classes


def _scan_module(module: Any, package_prefix: str, discovered_classes: list[type[BaseTool]]) -> None:
    """Scan attributes of a module for classes inheriting from BaseTool."""
    for attribute_name in dir(module):
        if attribute_name.startswith("_"):
            continue
        attribute_value = getattr(module, attribute_name)

        if (
            isinstance(attribute_value, type)
            and issubclass(attribute_value, BaseTool)
            and attribute_value is not BaseTool
        ):
            module_name = getattr(attribute_value, "__module__", "")
            if module_name.startswith(package_prefix) and attribute_value not in discovered_classes:
                discovered_classes.append(attribute_value)


def _resolve_parameter_dependency(
    parameter_name: str,
    parameter_object: inspect.Parameter,
    dependency_dictionary: dict[str, Any],
) -> tuple[bool, Any]:
    """Attempt to resolve a single parameter dependency from the dictionary.

    Returns a tuple of (success_boolean, resolved_value).
    """
    if parameter_name in dependency_dictionary:
        resolved_value = dependency_dictionary[parameter_name]
        if resolved_value is not None:
            return True, resolved_value

    parameter_annotation = parameter_object.annotation
    if parameter_annotation is not inspect.Parameter.empty:
        if isinstance(parameter_annotation, type):
            matching_dependency = next(
                (
                    dependency_value
                    for dependency_value in dependency_dictionary.values()
                    if dependency_value is not None and isinstance(dependency_value, parameter_annotation)
                ),
                None,
            )
            if matching_dependency is not None:
                return True, matching_dependency

        elif isinstance(parameter_annotation, str):
            target_class_name = parameter_annotation.split("|")[0].strip().split(".")[-1]
            matching_dependency = next(
                (
                    dependency_value
                    for dependency_value in dependency_dictionary.values()
                    if dependency_value is not None
                    and target_class_name in {base_class.__name__ for base_class in dependency_value.__class__.__mro__}
                ),
                None,
            )
            if matching_dependency is not None:
                return True, matching_dependency

    if parameter_object.default is not inspect.Parameter.empty:
        return True, None

    return False, None


def resolve_dependencies_and_instantiate(
    tool_class: type[BaseTool],
    dependency_dictionary: dict[str, Any],
) -> BaseTool | None:
    """Resolve class constructor arguments from the dependency dictionary and instantiate."""
    init_method = getattr(tool_class, "__init__", None)
    if init_method is None or init_method is object.__init__:
        return tool_class()

    constructor_signature = inspect.signature(init_method)
    argument_dictionary: dict[str, Any] = {}

    for parameter_name, parameter_object in constructor_signature.parameters.items():
        if parameter_name == "self":
            continue

        resolved_successfully, resolved_value = _resolve_parameter_dependency(
            parameter_name, parameter_object, dependency_dictionary
        )
        if not resolved_successfully:
            logger.debug(
                "Could not resolve dependency parameter '%s' for tool %s", parameter_name, tool_class.__name__
            )
            return None

        if resolved_value is not None:
            argument_dictionary[parameter_name] = resolved_value

    try:
        return tool_class(**argument_dictionary)
    except Exception as exception:
        logger.debug("Failed to instantiate tool class %s: %s", tool_class.__name__, exception)
        return None
