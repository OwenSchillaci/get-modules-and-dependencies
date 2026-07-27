from collections import defaultdict
import subprocess
import json


def get_module_catalog() -> list[dict[str, object]]:
    result = subprocess.run(
        ["bash", "-lc", "module --terse --redirect spider"],
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    )

    grouped: dict[str, list[str]] = defaultdict(list)

    for raw_line in result.stdout.splitlines():
        full_name = raw_line.strip()

        if not full_name or "/" not in full_name or full_name.endswith("/"):
            continue

        name, version = full_name.split("/", 1)
        if version == "":
            continue 
        grouped[name].append(version)

    return [
        {
            "name": name,
            "versions": sorted(set(versions)),
        }
        for name, versions in sorted(grouped.items())
    ]

modules = get_module_catalog()
with open('modules.json', 'w', encoding='utf-8') as f:
    json.dump(modules, f, indent=2)
print(f"Wrote {len(modules)} modules to modules.json")

