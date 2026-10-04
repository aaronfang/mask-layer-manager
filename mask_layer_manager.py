bl_info = {
    "name": "Sculpt Mask Layer Manager",
    "author": "OpenAI",
    "version": (1, 0, 0),
    "blender": (4, 5, 0),
    "location": "View3D > Sidebar > Sculpt > Mask Layers",
    "description": "Store and combine multiple continuous sculpt mask layers",
    "category": "Sculpt",
}

import bpy
import re
from array import array
from bpy.props import (
    BoolProperty,
    CollectionProperty,
    EnumProperty,
    IntProperty,
    StringProperty,
)

PREFIX = "masklib__"
SUSPENDED_ATTRIBUTE = PREFIX + "suspended"
_suspended_masks = {}
_addon_keymaps = []

KEY_ITEMS = [
    ('M', 'M', 'M key'),
    ('N', 'N', 'N key'),
    ('Q', 'Q', 'Q key'),
    ('F', 'F', 'F key'),
    ('X', 'X', 'X key'),
    ('Z', 'Z', 'Z key'),
    ('ONE', '1', '1 key'),
    ('TWO', '2', '2 key'),
    ('THREE', '3', '3 key'),
    ('FOUR', '4', '4 key'),
]


def _update_shortcut(self, context):
    for km, kmi in _addon_keymaps:
        kmi.type = self.shortcut_key
        kmi.ctrl = self.shortcut_ctrl
        kmi.shift = self.shortcut_shift
        kmi.alt = self.shortcut_alt


def _active_mesh(context):
    obj = context.object
    if not obj or obj.type != "MESH":
        raise RuntimeError("Select a mesh object")
    return obj


def _attribute_name(label):
    safe = re.sub(r"[^A-Za-z0-9_]+", "_", label).strip("_") or "layer"
    return PREFIX + safe


def _unique_attribute_name(mesh, label, ignore=None):
    base = _attribute_name(label)
    candidate = base
    n = 2
    while candidate in mesh.attributes and candidate != ignore:
        candidate = f"{base}_{n}"
        n += 1
    return candidate


def _enter_object_mode(obj):
    old_mode = obj.mode
    # Mesh attributes are writable in Sculpt Mode. Avoiding a mode switch keeps
    # the PBVH cache alive, which is important on multi-million vertex meshes.
    if old_mode not in {'OBJECT', 'SCULPT'}:
        bpy.ops.object.mode_set(mode='OBJECT')
    return old_mode


def _restore_mode(obj, old_mode):
    if old_mode != 'OBJECT' and obj and bpy.context.object == obj:
        try:
            bpy.ops.object.mode_set(mode=old_mode)
        except RuntimeError:
            pass


def _ensure_mask(mesh):
    attr = mesh.attributes.get(".sculpt_mask")
    if not attr:
        attr = mesh.attributes.new(".sculpt_mask", type='FLOAT', domain='POINT')
        attr.data.foreach_set("value", array('f', [0.0]) * len(attr.data))
    return attr


def _ensure_layer(mesh, item):
    attr = mesh.attributes.get(item.attribute_name)
    if not attr:
        attr = mesh.attributes.new(item.attribute_name, type='FLOAT', domain='POINT')
    return attr


def _clamp(value):
    return max(0.0, min(1.0, value))


def _read_values(attribute):
    """Read an attribute through Blender's bulk RNA API."""
    values = array('f', [0.0]) * len(attribute.data)
    attribute.data.foreach_get("value", values)
    return values


def _write_values(attribute, values):
    """Write an attribute through Blender's bulk RNA API."""
    attribute.data.foreach_set("value", values)


class SMLM_Layer(bpy.types.PropertyGroup):
    name: StringProperty(name="Layer", default="Mask")
    attribute_name: StringProperty(options={'HIDDEN'})


class SMLM_AddonPreferences(bpy.types.AddonPreferences):
    bl_idname = __name__

    shortcut_key: EnumProperty(
        name="Key",
        items=KEY_ITEMS,
        default='M',
        update=_update_shortcut,
    )
    shortcut_ctrl: BoolProperty(name="Ctrl", default=True, update=_update_shortcut)
    shortcut_shift: BoolProperty(name="Shift", default=True, update=_update_shortcut)
    shortcut_alt: BoolProperty(name="Alt", default=False, update=_update_shortcut)

    def draw(self, context):
        layout = self.layout
        layout.label(text="Suspend / Restore Mask Influence Shortcut")
        row = layout.row(align=True)
        row.prop(self, "shortcut_key", text="Key")
        row.prop(self, "shortcut_ctrl", toggle=True)
        row.prop(self, "shortcut_shift", toggle=True)
        row.prop(self, "shortcut_alt", toggle=True)
        layout.label(text="Shortcut changes apply immediately in the 3D View.", icon='INFO')


class SMLM_OT_store(bpy.types.Operator):
    bl_idname = "sculpt_mask_layers.store"
    bl_label = "Store Mask"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def description(cls, context, properties):
        if properties and properties.replace_selected:
            return "Update the selected layer with the current sculpt mask, preserving continuous 0-1 values"
        return "Store the current sculpt mask as a new named layer"

    new_name: StringProperty(name="Layer Name", default="Mask")
    replace_selected: BoolProperty(name="Replace Selected", default=False)

    def invoke(self, context, event):
        if context.object and context.object.mask_layer_index >= 0 and self.replace_selected:
            item = context.object.mask_layers[context.object.mask_layer_index]
            self.new_name = item.name
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        try:
            obj = _active_mesh(context)
            mesh = obj.data
            old_mode = _enter_object_mode(obj)
            src = _ensure_mask(mesh)
            item = None
            if self.replace_selected and 0 <= obj.mask_layer_index < len(obj.mask_layers):
                item = obj.mask_layers[obj.mask_layer_index]
            if item is None:
                item = obj.mask_layers.add()
                item.name = self.new_name.strip() or "Mask"
                item.attribute_name = _unique_attribute_name(mesh, item.name)
                obj.mask_layer_index = len(obj.mask_layers) - 1
            dst = _ensure_layer(mesh, item)
            if len(src.data) != len(dst.data):
                raise RuntimeError("Mask layer vertex count does not match this mesh")
            values = _read_values(src)
            _write_values(dst, values)
            mesh.update()
            self.report({'INFO'}, f"Stored mask: {item.name}")
            _restore_mode(obj, old_mode)
            return {'FINISHED'}
        except RuntimeError as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}


def _apply_layer(context, operation):
    obj = _active_mesh(context)
    if not (0 <= obj.mask_layer_index < len(obj.mask_layers)):
        raise RuntimeError("Select a mask layer first")
    mesh = obj.data
    old_mode = _enter_object_mode(obj)
    try:
        current = _ensure_mask(mesh)
        layer = _ensure_layer(mesh, obj.mask_layers[obj.mask_layer_index])
        if len(current.data) != len(layer.data):
            raise RuntimeError("Mask layer vertex count does not match this mesh")
        current_values = _read_values(current)
        layer_values = _read_values(layer)
        for i, a_value in enumerate(current_values):
            a = _clamp(a_value)
            b = _clamp(layer_values[i])
            if operation == 'OVERRIDE':
                current_values[i] = b
            elif operation == 'ADD':
                current_values[i] = _clamp(a + b)
            elif operation == 'SUBTRACT':
                current_values[i] = _clamp(a - b)
            elif operation == 'MULTIPLY':
                current_values[i] = a * b
            elif operation == 'MAX':
                current_values[i] = max(a, b)
            elif operation == 'MIN':
                current_values[i] = min(a, b)
        _write_values(current, current_values)
        mesh.update()
        obj.data.update()
    finally:
        _restore_mode(obj, old_mode)


class SMLM_OT_apply(bpy.types.Operator):
    bl_idname = "sculpt_mask_layers.apply"
    bl_label = "Apply Layer"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def description(cls, context, properties):
        descriptions = {
            'OVERRIDE': "Replace the current sculpt mask with the selected layer",
            'ADD': "Add the selected layer to the current mask and clamp values to 0-1",
            'SUBTRACT': "Subtract the selected layer from the current mask and clamp values to 0-1",
            'MULTIPLY': "Multiply the current mask by the selected layer",
            'MAX': "Keep the stronger value at each vertex from the current mask and selected layer",
            'MIN': "Keep the weaker value at each vertex from the current mask and selected layer",
        }
        return descriptions.get(properties.operation, "Apply the selected mask layer")

    operation: EnumProperty(
        items=[
            ('OVERRIDE', "Override", "Replace current mask"),
            ('ADD', "Add", "Add layer and clamp to 1"),
            ('SUBTRACT', "Subtract", "Subtract layer and clamp to 0"),
            ('MULTIPLY', "Multiply", "Multiply mask values"),
            ('MAX', "Max", "Keep the stronger mask value"),
            ('MIN', "Min", "Keep the weaker mask value"),
        ], default='OVERRIDE')

    def execute(self, context):
        try:
            _apply_layer(context, self.operation)
            return {'FINISHED'}
        except RuntimeError as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}


class SMLM_OT_delete(bpy.types.Operator):
    bl_idname = "sculpt_mask_layers.delete"
    bl_label = "Delete Layer"
    bl_options = {'REGISTER', 'UNDO'}

    bl_description = "Delete the selected mask layer and its stored vertex attribute"

    def execute(self, context):
        try:
            obj = _active_mesh(context)
            if not (0 <= obj.mask_layer_index < len(obj.mask_layers)):
                raise RuntimeError("Select a mask layer first")
            item = obj.mask_layers[obj.mask_layer_index]
            old_mode = _enter_object_mode(obj)
            if item.attribute_name in obj.data.attributes:
                obj.data.attributes.remove(obj.data.attributes[item.attribute_name])
            obj.mask_layers.remove(obj.mask_layer_index)
            obj.mask_layer_index = min(obj.mask_layer_index, len(obj.mask_layers) - 1)
            obj.data.update()
            _restore_mode(obj, old_mode)
            return {'FINISHED'}
        except RuntimeError as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}


class SMLM_OT_duplicate(bpy.types.Operator):
    bl_idname = "sculpt_mask_layers.duplicate"
    bl_label = "Duplicate Layer"
    bl_options = {'REGISTER', 'UNDO'}

    bl_description = "Duplicate the selected mask layer as a new independent layer"

    def execute(self, context):
        try:
            obj = _active_mesh(context)
            if not (0 <= obj.mask_layer_index < len(obj.mask_layers)):
                raise RuntimeError("Select a mask layer first")
            source = obj.mask_layers[obj.mask_layer_index]
            old_mode = _enter_object_mode(obj)
            item = obj.mask_layers.add()
            item.name = source.name + " copy"
            item.attribute_name = _unique_attribute_name(obj.data, item.name)
            src = _ensure_layer(obj.data, source)
            dst = _ensure_layer(obj.data, item)
            if len(src.data) != len(dst.data):
                raise RuntimeError("Mask layer vertex count does not match this mesh")
            _write_values(dst, _read_values(src))
            obj.mask_layer_index = len(obj.mask_layers) - 1
            obj.data.update()
            _restore_mode(obj, old_mode)
            return {'FINISHED'}
        except RuntimeError as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}


class SMLM_OT_clear(bpy.types.Operator):
    bl_idname = "sculpt_mask_layers.clear"
    bl_label = "Clear Current Mask"
    bl_options = {'REGISTER', 'UNDO'}

    bl_description = "Set the current sculpt mask to zero (unmasked)"

    def execute(self, context):
        try:
            obj = _active_mesh(context)
            old_mode = _enter_object_mode(obj)
            mask = _ensure_mask(obj.data)
            _write_values(mask, array('f', [0.0]) * len(mask.data))
            obj.data.update()
            _restore_mode(obj, old_mode)
            return {'FINISHED'}
        except RuntimeError as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}


class SMLM_OT_invert(bpy.types.Operator):
    bl_idname = "sculpt_mask_layers.invert"
    bl_label = "Invert Current Mask"
    bl_options = {'REGISTER', 'UNDO'}

    bl_description = "Invert the current sculpt mask: 0 becomes 1 and 1 becomes 0"

    def execute(self, context):
        try:
            obj = _active_mesh(context)
            old_mode = _enter_object_mode(obj)
            mask = _ensure_mask(obj.data)
            values = _read_values(mask)
            for i, value in enumerate(values):
                values[i] = 1.0 - _clamp(value)
            _write_values(mask, values)
            obj.data.update()
            _restore_mode(obj, old_mode)
            return {'FINISHED'}
        except RuntimeError as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}


class SMLM_OT_toggle_suspend(bpy.types.Operator):
    bl_idname = "sculpt_mask_layers.toggle_suspend"
    bl_label = "Suspend Mask Influence"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def description(cls, context, properties):
        if context and context.object and context.object.mask_influence_suspended:
            return "Restore the previously active sculpt mask and resume mask influence on brushes"
        return "Temporarily disable sculpt mask influence while preserving the current mask for later restore"

    def execute(self, context):
        try:
            obj = _active_mesh(context)
            old_mode = _enter_object_mode(obj)
            mesh = obj.data
            mask = _ensure_mask(mesh)
            cache_key = obj.as_pointer()
            if not obj.mask_influence_suspended:
                # Keep the temporary copy out of Mesh Attributes. During
                # Sculpt/PBVH updates Blender can expose a transient point
                # count for newly-created attributes.
                _suspended_masks[cache_key] = _read_values(mask)
                _write_values(mask, array('f', [0.0]) * len(mask.data))
                obj.mask_influence_suspended = True
                self.report({'INFO'}, "Mask influence suspended")
            else:
                saved = _suspended_masks.get(cache_key)
                if saved is None or len(saved) != len(mask.data):
                    raise RuntimeError("No compatible suspended mask found; suspend the mask again")
                _write_values(mask, saved)
                _suspended_masks.pop(cache_key, None)
                obj.mask_influence_suspended = False
                self.report({'INFO'}, "Mask influence restored")
            mesh.update()
            obj.data.update()
            _restore_mode(obj, old_mode)
            return {'FINISHED'}
        except RuntimeError as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}


class SMLM_OT_rename(bpy.types.Operator):
    bl_idname = "sculpt_mask_layers.rename"
    bl_label = "Rename Layer"
    bl_options = {'REGISTER', 'UNDO'}

    bl_description = "Rename the selected mask layer"

    new_name: StringProperty(name="Layer Name")

    def invoke(self, context, event):
        obj = context.object
        if obj and 0 <= obj.mask_layer_index < len(obj.mask_layers):
            self.new_name = obj.mask_layers[obj.mask_layer_index].name
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        obj = context.object
        if not obj or not (0 <= obj.mask_layer_index < len(obj.mask_layers)):
            self.report({'ERROR'}, "Select a mask layer first")
            return {'CANCELLED'}
        obj.mask_layers[obj.mask_layer_index].name = self.new_name.strip() or "Mask"
        return {'FINISHED'}


class SMLM_UL_layers(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        layout.label(text=item.name, icon='MOD_MASK')


class SMLM_PT_panel(bpy.types.Panel):
    bl_label = "Mask Layers"
    bl_idname = "SMLM_PT_panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Sculpt"

    @classmethod
    def poll(cls, context):
        return context.object and context.object.type == 'MESH'

    def draw(self, context):
        layout = self.layout
        obj = context.object
        row = layout.row()
        row.template_list("SMLM_UL_layers", "mask_layers", obj, "mask_layers", obj, "mask_layer_index", rows=4)
        col = row.column(align=True)
        store = col.operator("sculpt_mask_layers.store", text="", icon='ADD')
        store.replace_selected = False
        overwrite = col.operator("sculpt_mask_layers.store", text="", icon='FILE_REFRESH')
        overwrite.replace_selected = True
        col.operator("sculpt_mask_layers.duplicate", text="", icon='DUPLICATE')
        col.operator("sculpt_mask_layers.rename", text="", icon='GREASEPENCIL')
        col.operator("sculpt_mask_layers.delete", text="", icon='TRASH')

        box = layout.box()
        box.label(text="Apply selected layer")
        row = box.row(align=True)
        for op, label, icon in [
            ('OVERRIDE', "Override", 'FILE_REFRESH'),
            ('ADD', "Add", 'ADD'),
            ('SUBTRACT', "Subtract", 'REMOVE'),
        ]:
            p = row.operator("sculpt_mask_layers.apply", text=label, icon=icon)
            p.operation = op
        row = box.row(align=True)
        for op, label in [('MULTIPLY', "Multiply"), ('MAX', "Max"), ('MIN', "Min")]:
            p = row.operator("sculpt_mask_layers.apply", text=label)
            p.operation = op

        row = layout.row(align=True)
        row.operator("sculpt_mask_layers.clear", text="Clear", icon='X')
        row.operator("sculpt_mask_layers.invert", text="Invert", icon='ARROW_LEFTRIGHT')
        suspend = layout.operator(
            "sculpt_mask_layers.toggle_suspend",
            text="",
            icon='HIDE_OFF' if obj.mask_influence_suspended else 'HIDE_ON',
        )
        layout.label(text="Layers store continuous 0-1 vertex mask values.", icon='INFO')


classes = (
    SMLM_AddonPreferences,
    SMLM_Layer,
    SMLM_OT_store,
    SMLM_OT_apply,
    SMLM_OT_delete,
    SMLM_OT_duplicate,
    SMLM_OT_clear,
    SMLM_OT_invert,
    SMLM_OT_toggle_suspend,
    SMLM_OT_rename,
    SMLM_UL_layers,
    SMLM_PT_panel,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Object.mask_layers = CollectionProperty(type=SMLM_Layer)
    bpy.types.Object.mask_layer_index = IntProperty(default=-1)
    bpy.types.Object.mask_influence_suspended = BoolProperty(default=False)
    addon_entry = bpy.context.preferences.addons.get(__name__)
    prefs = addon_entry.preferences if addon_entry else None
    shortcut_key = prefs.shortcut_key if prefs else 'M'
    shortcut_ctrl = prefs.shortcut_ctrl if prefs else True
    shortcut_shift = prefs.shortcut_shift if prefs else True
    shortcut_alt = prefs.shortcut_alt if prefs else False
    wm = bpy.context.window_manager
    kc = wm.keyconfigs.addon if wm else None
    if kc:
        km = kc.keymaps.new(name='3D View', space_type='VIEW_3D')
        kmi = km.keymap_items.new(
            "sculpt_mask_layers.toggle_suspend",
            shortcut_key,
            'PRESS',
            ctrl=shortcut_ctrl,
            shift=shortcut_shift,
            alt=shortcut_alt,
        )
        _addon_keymaps.append((km, kmi))


def unregister():
    for km, kmi in _addon_keymaps:
        km.keymap_items.remove(kmi)
    _addon_keymaps.clear()
    del bpy.types.Object.mask_layer_index
    del bpy.types.Object.mask_layers
    del bpy.types.Object.mask_influence_suspended
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)


if __name__ == "__main__":
    register()
