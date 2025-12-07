"""
Demo app for testing text provider selection.

This app demonstrates the text_provider property on kivy.uix.label.Label,
allowing you to select different text rendering backends at runtime.
"""

from kivy.app import App
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.label import Label
from kivy.uix.spinner import Spinner
from kivy.core.text import LabelBase
from kivy.lang import Builder

KV = '''
BoxLayout:
    orientation: 'vertical'
    padding: 20
    spacing: 20

    Label:
        id: demo_label
        text: '[color=ff6600]Test[/color] the [b]text[/b] [i][color=00aaff]provider[/color][/i]'
        markup: True
        font_size: '32sp'

    BoxLayout:
        size_hint_y: None
        height: '48dp'
        spacing: 10

        Label:
            text: 'Text Provider:'
            size_hint_x: 0.3

        Spinner:
            id: provider_spinner
            text: 'default'
            size_hint_x: 0.7
            on_text: app.provider_changed(self.text)

    Label:
        id: status_label
        text: ''
        font_size: '14sp'
        size_hint_y: None
        height: '48dp'
        color: 0.7, 0.7, 0.7, 1
'''


class TextProviderDemoApp(App):
    def build(self):
        self.root = Builder.load_string(KV)

        # Get available providers and populate spinner
        providers = ['default'] + LabelBase.available_providers()
        self.root.ids.provider_spinner.values = providers

        # Show initial status
        self.update_status()

        return self.root

    def provider_changed(self, provider_name):
        """Handle provider selection from spinner."""
        demo_label = self.root.ids.demo_label

        if provider_name == 'default':
            demo_label.text_provider = None
        else:
            demo_label.text_provider = provider_name

        self.update_status()

    def update_status(self):
        """Update status label with current provider info."""
        demo_label = self.root.ids.demo_label
        status_label = self.root.ids.status_label

        # Get the actual class being used
        if demo_label._label:
            class_name = demo_label._label.__class__.__name__
        else:
            class_name = 'Not initialized'

        current_provider = demo_label.text_provider or 'default'
        available = LabelBase.available_providers()

        status_label.text = (
            f"Current provider: {current_provider}\n"
            f"Internal class: {class_name}\n"
            f"Available providers: {', '.join(available)}"
        )


if __name__ == '__main__':
    TextProviderDemoApp().run()

