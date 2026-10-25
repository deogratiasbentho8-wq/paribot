"""Application Android 'Paris Bot' (Kivy) - utilise le modèle de bot_paris.py."""
import os
import threading

from kivy.app import App
from kivy.clock import Clock
from kivy.core.window import Window
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.label import Label
from kivy.uix.scrollview import ScrollView

import bot_paris

Window.clearcolor = (0.07, 0.08, 0.10, 1)


class ParisBotApp(App):
    title = "Paris Bot"

    def build(self):
        data_dir = self.user_data_dir
        bot_paris.CACHE = os.path.join(data_dir, "cache")
        bot_paris.REPORTS = os.path.join(data_dir, "reports")
        self.last_file = os.path.join(data_dir, "dernier_rapport.txt")

        root = BoxLayout(orientation="vertical", padding=12, spacing=10)
        root.add_widget(Label(text="[b]PARIS DU JOUR[/b]", markup=True, font_size="22sp",
                              size_hint_y=None, height=50, color=(1, 0.85, 0.3, 1)))

        row = BoxLayout(size_hint_y=None, height=56, spacing=10)
        for text, days in (("Aujourd'hui", 1), ("3 jours", 3)):
            b = Button(text=text, font_size="17sp", background_normal="",
                       background_color=(0.15, 0.55, 0.35, 1))
            b.bind(on_release=lambda _b, d=days: self.analyse(d))
            row.add_widget(b)
        root.add_widget(row)

        self.scroll = ScrollView()
        self.out = Label(text=self.load_last(), font_size="15sp", size_hint_y=None,
                         halign="left", valign="top", color=(0.92, 0.92, 0.92, 1))
        self.out.bind(width=lambda *_: setattr(self.out, "text_size", (self.out.width - 10, None)),
                      texture_size=lambda *_: setattr(self.out, "height", self.out.texture_size[1] + 20))
        self.scroll.add_widget(self.out)
        root.add_widget(self.scroll)
        return root

    def load_last(self):
        if os.path.exists(self.last_file):
            with open(self.last_file, encoding="utf-8") as f:
                return "(Dernier rapport enregistré)\n\n" + f.read()
        return "Appuie sur « Aujourd'hui » pour lancer l'analyse.\n(Connexion internet nécessaire.)"

    def analyse(self, days):
        self.out.text = "Analyse en cours... (10 à 40 secondes)"
        threading.Thread(target=self._work, args=(days,), daemon=True).start()

    def _work(self, days):
        try:
            report = bot_paris.run_analysis(days=days)
            with open(self.last_file, "w", encoding="utf-8") as f:
                f.write(report)
        except Exception as e:  # affichage lisible des erreurs
            report = f"Erreur : {e}\nVérifie ta connexion internet puis réessaie."
        Clock.schedule_once(lambda _dt: self._show(report))

    def _show(self, text):
        self.out.text = text
        self.scroll.scroll_y = 1


if __name__ == "__main__":
    ParisBotApp().run()
