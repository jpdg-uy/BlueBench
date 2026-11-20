"""Protocol extensions loaded at start-up (see ``bluebench.ble.registry``).

Each entry is a module name; importing it registers its decoders and names.
"""
MODULES: list[str] = []
