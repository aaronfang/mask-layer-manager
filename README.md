# Sculpt Mask Layer Manager

Blender 4.5 add-on for storing and combining multiple continuous sculpt mask layers.

## Features

- Store and update named mask layers as continuous `FLOAT / POINT` mesh attributes.
- Override, add, subtract, multiply, max, and min operations.
- Duplicate, rename, delete, clear, and invert layers.
- Temporarily suspend mask influence while preserving the current mask.
- Configurable shortcut for suspending/restoring mask influence.

## Installation

In Blender, open `Edit > Preferences > Add-ons > Install`, choose `mask_layer_manager.py`, and enable the add-on. The panel is available in the 3D View sidebar under `Sculpt > Mask Layers`.

## Notes

Mask layers are vertex-domain floating-point attributes. Topology-changing operations such as remeshing or retopology invalidate existing layer correspondence.
