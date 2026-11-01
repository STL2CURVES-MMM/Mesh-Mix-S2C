"""Credits: who makes the app and the people who support it.

Opened from the Credits button in the Software Update window. Supporters who
gave a name for the app are listed by it; everyone else is an anonymous
supporter. Add new names to SUPPORTERS (and to Credits.supporters in the Mac
app's UpdateView.swift, which must match)."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QVBoxLayout

from . import SUPPORT_URL, system, theme
from .theme import BOLD, SEMIBOLD
from .widgets import AccentButton, Card, GhostButton, IconLabel, Label, SectionLabel, hbox, vbox

# Each supporter's name as they asked for it to appear, or None for an anonymous one
SUPPORTERS = [None, None, None]

ANONYMOUS = "Anonymous supporter"

MAKERS = [
    ("Tuna", "Manifold Mesh Mix: the app, its checks and repairs"),
    ("Duvdivan", "stl2curves: finding threads, fillets and freeform areas in the mesh"),
]

NAME_NOTE = ("Want your name here? When you donate, write the name you'd like shown in the app in your "
             "message. If you don't give one, you're listed as an anonymous supporter.")


def supporter_names():
    return [n.strip() if n and n.strip() else ANONYMOUS for n in SUPPORTERS]


class CreditsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Credits")
        self.setFixedWidth(440)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 20, 20, 20)
        lay.setSpacing(14)
        head = hbox(IconLabel("heart", theme.accent(), 28, weight=1.8),
                    vbox(Label("Credits", 15, BOLD),
                         Label("Thank you to everyone who makes Mesh Mix-S2C possible.", 12,
                               color=theme.TEXT_DIM, wrap=True), spacing=4), spacing=14)
        lay.addLayout(head)

        lay.addWidget(SectionLabel("Made by"))
        makers = Card(padding=12, radius=theme.R_BIG, spacing=10)
        for name, what in MAKERS:
            makers.lay.addLayout(vbox(Label(name, 12.5, SEMIBOLD), Label(what, 11, color=theme.TEXT_DIM, wrap=True), spacing=2))
        lay.addWidget(makers)

        lay.addWidget(SectionLabel("Supporters"))
        people = Card(padding=12, radius=theme.R_BIG)
        for name in supporter_names():
            people.lay.addLayout(hbox(IconLabel("heart", theme.accent(), 14, weight=2.0),
                                      Label(name, 12, color=theme.TEXT_DIM if name == ANONYMOUS else theme.TEXT),
                                      None, spacing=8))
        lay.addWidget(people)
        lay.addWidget(Label(NAME_NOTE, 11, color=theme.TEXT_DIM, wrap=True))

        support = AccentButton("Support the Project", "heart", expand=False)
        support.clicked.connect(lambda: system.open_url(SUPPORT_URL))
        close = GhostButton("Close")
        close.clicked.connect(self.accept)
        close.setDefault(True)
        lay.addLayout(hbox(None, close, support))
        self.layout().setAlignment(Qt.AlignmentFlag.AlignTop)
