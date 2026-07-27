import subprocess
import json

def get_all_modules() -> list[dict[str, str]]:
    result = subprocess.run(
        ["bash", "-lc", "module --terse --redirect spider"],
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    )

    modules = []

    for line in result.stdout.splitlines():
        full_name = line.strip()

        if not full_name or "/" not in full_name or full_name.endswith("/"):
            continue

        name, version = full_name.split("/", 1)
        if version == "":
            continue

        modules.append(
            {
                "name": name,
                "version": version,
                "full_name": full_name,
            }
        )

    return modules

modules = get_all_modules()
with open('modules.json', 'w', encoding='utf-8') as f:
    json.dump(modules, f, indent=2)
print(f"Wrote {len(modules)} modules to modules.json")
