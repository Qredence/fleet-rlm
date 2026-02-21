with open("src/fleet_rlm/server/routers/ws.py", "r") as f:
    lines = f.readlines()

new_lines = []
in_with = False
for i, line in enumerate(lines):
    if line.strip() == "dspy.context(lm=_planner_lm),":
        new_lines.append(line)
        continue
    if line.strip() == "agent_context as agent,":
        continue
    if line.strip() == "):" and lines[i - 1].strip() == "agent_context as agent,":
        new_lines.append("    ):\n")
        new_lines.append("        async with agent_context as agent:\n")
        in_with = True
        continue

    if line.startswith("async def _handle_command"):
        in_with = False

    if line.startswith("def _volume_load_manifest"):
        line = line.replace(
            "def _volume_load_manifest", "async def _volume_load_manifest"
        )
    if line.startswith("def _volume_save_manifest"):
        line = line.replace(
            "def _volume_save_manifest", "async def _volume_save_manifest"
        )

    if "agent.interpreter.execute(" in line and "SUBMIT(" in line:
        line = line.replace(
            "agent.interpreter.execute(", "await agent.interpreter.aexecute("
        )

    if "remote_manifest = _volume_load_manifest" in line:
        line = line.replace("_volume_load_manifest", "await _volume_load_manifest")
    if "manifest = (" in line and "_volume_load_manifest" in lines[i + 1]:
        pass
    if "_volume_load_manifest(agent, manifest_path)" in line and "await" not in line:
        line = line.replace("_volume_load_manifest", "await _volume_load_manifest")
    if "saved_path = _volume_save_manifest" in line:
        line = line.replace("_volume_save_manifest", "await _volume_save_manifest")

    if in_with and line != "\n":
        new_lines.append("    " + line)
    else:
        new_lines.append(line)

with open("src/fleet_rlm/server/routers/ws.py", "w") as f:
    f.writelines(new_lines)
