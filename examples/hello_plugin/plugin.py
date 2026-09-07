"""Example action plugin.

Copy this folder to ~/.local/share/PopStream/plugins/hello/
"""

from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from popstream.core.plugin import Action, ActionContext, ActionInfo, Plugin


class HelloAction(Action):
    def key_down(self, ctx: ActionContext) -> None:
        ctx.log("Hello from an external plugin")
        ctx.show_ok()

    def create_property_inspector(self, ctx: ActionContext, parent: QWidget) -> QWidget:
        widget = QWidget(parent)
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(QLabel("This action is loaded from examples/hello_plugin."))
        return widget


class HelloPlugin(Plugin):
    id = "com.popstream.example.hello"
    name = "Hello"
    version = "0.1.0"
    author = "PopStream"

    def actions(self):
        return [ActionInfo("hello", "Hello", "Examples", "Shows OK on the key", "check")]

    def create_action(self, action_id: str):
        return HelloAction() if action_id == "hello" else None


plugin = HelloPlugin()
