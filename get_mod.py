import argparse
import json
import os
import re
import subprocess
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Any


OUTPUT_FILE = "modules.json"
DEFAULT_WORKERS = 5
COMMAND_TIMEOUT = 60

EXTENSION_PATTERN = re.compile(r"^(?P<full_name>.+?)\s+\(E\)$")
EXTENSION_PROVIDER_PATTERN = re.compile(
    r"^(?P<provider>\S+/\S+)(?:\s+\((?P<hierarchy>[^)]+)\))?$"
)
PREREQUISITE_HEADING = "You will need to load all module(s)"
EXTENSION_HEADING = "This extension is provided by the following modules"
DESCRIPTION_HEADING = "Description:"


def parse_module_line(raw_line: str) -> dict[str, Any] | None:
    line = raw_line.strip()

    if not line or "/" not in line or line.endswith("/"):
        return None

    extension_match = EXTENSION_PATTERN.match(line)
    if extension_match:
        full_name = extension_match.group("full_name")
        is_extension = True
    else:
        full_name = line
        is_extension = False

    name, version = full_name.split("/", 1)
    if not version:
        return None

    return {
        "name": name,
        "version": version,
        "full_name": full_name,
        "is_extension": is_extension,
    }


def get_all_modules() -> list[dict[str, Any]]:
    result = subprocess.run(
        ["bash", "-lc", "module --terse --redirect spider"],
        capture_output=True,
        text=True,
        check=True,
        timeout=COMMAND_TIMEOUT,
    )

    modules = []
    for raw_line in result.stdout.splitlines():
        module = parse_module_line(raw_line)
        if module is not None:
            modules.append(module)
    return modules


def parse_module_details(
    output: str,
) -> tuple[list[list[str]], bool, str | None]:
    dependencies: list[list[str]] = []
    is_extension = False
    section: str | None = None
    section_has_data = False
    description_lines: list[str] = []
    description_finished = False

    for raw_line in output.splitlines():
        line = raw_line.strip()

        # Only use Lmod's top-level Description section. The Help section can
        # contain another "Description" heading with different formatting.
        if line == DESCRIPTION_HEADING and not description_finished:
            section = "description"
            section_has_data = False
            continue

        if PREREQUISITE_HEADING in line:
            if section == "description":
                description_finished = True
            section = "prerequisites"
            section_has_data = False
            continue

        if EXTENSION_HEADING in line:
            if section == "description":
                description_finished = True
            section = "extensions"
            section_has_data = False
            is_extension = True
            continue

        if EXTENSION_PATTERN.match(line):
            is_extension = True

        if section is None:
            continue

        if not line:
            if section_has_data:
                if section == "description":
                    description_finished = True
                section = None
            continue

        if section == "description":
            description_lines.append(line)
            section_has_data = True
            continue

        if section == "prerequisites":
            modules = line.split()
            if modules and all("/" in module for module in modules):
                dependencies.append(modules)
                section_has_data = True
        else:
            provider_match = EXTENSION_PROVIDER_PATTERN.match(line)
            if provider_match:
                provider = provider_match.group("provider")
                hierarchy = provider_match.group("hierarchy")
                path = hierarchy.split() if hierarchy else []
                path.append(provider)
                dependencies.append(path)
                section_has_data = True

    unique_dependencies = []
    seen = set()
    for dependency_path in dependencies:
        key = tuple(dependency_path)
        if key not in seen:
            seen.add(key)
            unique_dependencies.append(dependency_path)

    description = " ".join(description_lines) or None
    return unique_dependencies, is_extension, description


def enrich_module(module: dict[str, Any]) -> tuple[dict[str, Any], int]:
    enriched = module.copy()

    try:
        result = subprocess.run(
            [
                "bash",
                "-lc",
                "module --redirect spider \"$1\"",
                "bash",
                module["full_name"],
            ],
            capture_output=True,
            text=True,
            check=True,
            timeout=COMMAND_TIMEOUT,
        )
        dependencies, is_extension, description = parse_module_details(
            result.stdout
        )
        enriched["is_extension"] = enriched["is_extension"] or is_extension
        enriched["dependencies"] = dependencies
        enriched["description"] = description
    except subprocess.TimeoutExpired:
        enriched["dependencies"] = []
        enriched["description"] = None
        enriched["error"] = (
            f"Lookup timed out after {COMMAND_TIMEOUT} seconds"
        )
    except subprocess.CalledProcessError as error:
        enriched["dependencies"] = []
        enriched["description"] = None
        message = error.stderr.strip() or "No error output"
        enriched["error"] = (
            f"Lookup failed with exit code {error.returncode}: {message}"
        )
    except OSError as error:
        enriched["dependencies"] = []
        enriched["description"] = None
        enriched["error"] = f"Could not run module lookup: {error}"

    return enriched, os.getpid()


def positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return parsed


def allocated_workers() -> int:
    """Use the Slurm CPU allocation when running inside a submitted job."""
    slurm_cpus = os.environ.get("SLURM_CPUS_PER_TASK")
    if slurm_cpus:
        try:
            return positive_int(slurm_cpus)
        except (ValueError, argparse.ArgumentTypeError):
            pass
    return DEFAULT_WORKERS


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Collect Lmod module descriptions and dependency information."
    )
    parser.add_argument(
        "--workers",
        type=positive_int,
        default=allocated_workers(),
        help=(
            "number of worker processes (default: SLURM_CPUS_PER_TASK, "
            f"otherwise {DEFAULT_WORKERS})"
        ),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(OUTPUT_FILE),
        help=f"output JSON path (default: {OUTPUT_FILE})",
    )
    parser.add_argument(
        "--progress-every",
        type=positive_int,
        default=25,
        metavar="N",
        help="print worker progress every N completed lookups (default: 25)",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    modules = get_all_modules()
    total = len(modules)
    enriched_modules: list[dict[str, Any] | None] = [None] * total
    worker_counts: Counter[int] = Counter()

    print(
        f"Found {total} modules; starting {args.workers} worker processes",
        flush=True,
    )
    with ProcessPoolExecutor(max_workers=args.workers) as executor:
        futures = {
            executor.submit(enrich_module, module): index
            for index, module in enumerate(modules)
        }
        for completed, future in enumerate(as_completed(futures), start=1):
            index = futures[future]
            enriched_module, worker_pid = future.result()
            enriched_modules[index] = enriched_module
            worker_counts[worker_pid] += 1
            if completed % args.progress_every == 0 or completed == total:
                workers = ", ".join(
                    f"pid {pid}: {count}"
                    for pid, count in sorted(worker_counts.items())
                )
                print(
                    f"Completed {completed}/{total} ({workers})",
                    flush=True,
                )

    with args.output.open("w", encoding="utf-8") as output_file:
        json.dump(enriched_modules, output_file, indent=2)
        output_file.write("\n")

    print(f"Wrote {total} modules to {args.output}", flush=True)


if __name__ == "__main__":
    main()
