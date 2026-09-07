from __future__ import annotations

from PySide6.QtWidgets import QFormLayout, QLineEdit, QWidget

from popstream.core.plugin import Action, ActionContext, ActionInfo, Plugin
from popstream.ui.theme import tighten_form


class FolderAction(Action):
    def will_appear(self, ctx: ActionContext) -> None:
        if not ctx.settings.get("page_id"):
            page_id = ctx.create_folder_page(ctx.settings.get("name") or "Folder")
            settings = dict(ctx.settings)
            settings["page_id"] = page_id
            ctx.set_settings(settings)

    def key_down(self, ctx: ActionContext) -> None:
        page_id = ctx.settings.get("page_id")
        if not page_id:
            page_id = ctx.create_folder_page("Folder")
            settings = dict(ctx.settings)
            settings["page_id"] = page_id
            ctx.set_settings(settings)
        ctx.push_page(page_id)

    def create_property_inspector(self, ctx: ActionContext, parent: QWidget) -> QWidget:
        return _FolderInspector(ctx, parent)


class _FolderInspector(QWidget):
    def __init__(self, ctx: ActionContext, parent=None) -> None:
        super().__init__(parent)
        self._ctx = ctx
        layout = QFormLayout(self)
        tighten_form(layout)
        self.name = QLineEdit(ctx.settings.get("name", "Folder"))
        self.name.editingFinished.connect(self._save)
        layout.addRow("Name", self.name)

    def _save(self) -> None:
        s = dict(self._ctx.settings)
        s["name"] = self.name.text().strip() or "Folder"
        self._ctx.set_settings(s)


class BackAction(Action):
    def key_down(self, ctx: ActionContext) -> None:
        ctx.pop_page()


class NextPageAction(Action):
    def key_down(self, ctx: ActionContext) -> None:
        ctx.next_page()


class PrevPageAction(Action):
    def key_down(self, ctx: ActionContext) -> None:
        ctx.prev_page()


class NavigationPlugin(Plugin):
    id = "com.popstream.navigation"
    name = "Navigation"
    version = "0.1.0"
    author = "PopStream"

    def actions(self) -> list[ActionInfo]:
        return [
            ActionInfo("folder", "Folder", "Navigation", "Open a nested page of keys", "folder"),
            ActionInfo("back", "Back", "Navigation", "Leave the current folder", "back"),
            ActionInfo("next-page", "Next Page", "Navigation", "Cycle forward", "next"),
            ActionInfo("prev-page", "Previous Page", "Navigation", "Cycle backward", "prev"),
        ]

    def create_action(self, action_id: str) -> Action | None:
        mapping = {
            "folder": FolderAction,
            "back": BackAction,
            "next-page": NextPageAction,
            "prev-page": PrevPageAction,
        }
        cls = mapping.get(action_id)
        return cls() if cls else None
