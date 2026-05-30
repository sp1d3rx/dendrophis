from dendrophis.ui.widgets.panels.registry import PanelRegistry

print("Starting discovery...")
PanelRegistry.discover()
print(f"Registered IDs: {PanelRegistry.ids()}")
print(f"All Panels: {PanelRegistry.all()}")

# Check if SysInfoPanel is in the dict
found = False
for pid, pcls in PanelRegistry.all().items():
    if "sys_info" in pid:
        print(f"FOUND: {pid} -> {pcls}")
        found = True
if not found:
    print("NOT FOUND sys_info")
