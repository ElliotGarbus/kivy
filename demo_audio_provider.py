"""
Demo app for testing audio provider selection.

This app demonstrates the audio_output_provider parameter on SoundLoader.load(),
allowing you to select different audio backends at runtime.
"""

from kivy.app import App
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.label import Label
from kivy.uix.spinner import Spinner
from kivy.uix.textinput import TextInput
from kivy.core.audio_output import SoundLoader
from kivy.lang import Builder
from kivy.properties import ObjectProperty

KV = '''
BoxLayout:
    orientation: 'vertical'
    padding: 20
    spacing: 15

    Label:
        text: 'Audio Provider Demo'
        font_size: '24sp'
        size_hint_y: None
        height: '40dp'

    BoxLayout:
        size_hint_y: None
        height: '40dp'
        spacing: 10

        Label:
            text: 'Audio File:'
            size_hint_x: 0.2

        TextInput:
            id: file_input
            text: 'examples/audio/12908_sweet_trip_mm_clap_hi.wav'
            multiline: False
            size_hint_x: 0.8

    BoxLayout:
        size_hint_y: None
        height: '40dp'
        spacing: 10

        Label:
            text: 'Provider:'
            size_hint_x: 0.2

        Spinner:
            id: provider_spinner
            text: 'default'
            size_hint_x: 0.8

    BoxLayout:
        size_hint_y: None
        height: '50dp'
        spacing: 10

        Button:
            text: 'Load'
            on_press: app.load_sound()

        Button:
            text: 'Play'
            on_press: app.play_sound()

        Button:
            text: 'Stop'
            on_press: app.stop_sound()

    Label:
        id: status_label
        text: 'No sound loaded'
        font_size: '14sp'
        text_size: self.size
        halign: 'left'
        valign: 'top'
        color: 0.7, 0.7, 0.7, 1
'''


class AudioProviderDemoApp(App):
    sound = ObjectProperty(None, allownone=True)

    def build(self):
        self.root = Builder.load_string(KV)

        # Get available providers and populate spinner
        providers = ['default'] + SoundLoader.available_providers()
        self.root.ids.provider_spinner.values = providers

        self.update_status('Ready. Select a provider and load an audio file.')

        return self.root

    def load_sound(self):
        """Load sound with selected provider."""
        # Stop and unload current sound
        if self.sound:
            self.sound.stop()
            self.sound = None

        file_path = self.root.ids.file_input.text.strip()
        provider = self.root.ids.provider_spinner.text

        if not file_path:
            self.update_status('Error: Please enter a file path')
            return

        try:
            if provider == 'default':
                self.sound = SoundLoader.load(file_path)
            else:
                self.sound = SoundLoader.load(file_path, audio_output_provider=provider)

            if self.sound:
                self.update_status(
                    f"Loaded: {file_path}\n"
                    f"Provider: {provider}\n"
                    f"Sound class: {self.sound.__class__.__name__}\n"
                    f"Length: {self.sound.length:.2f}s" if self.sound.length else
                    f"Loaded: {file_path}\n"
                    f"Provider: {provider}\n"
                    f"Sound class: {self.sound.__class__.__name__}\n"
                    f"Length: unknown"
                )
            else:
                self.update_status(
                    f"Failed to load: {file_path}\n"
                    f"Provider: {provider}\n"
                    f"The provider may not support this file format."
                )
        except Exception as e:
            self.update_status(f"Error loading sound:\n{e}")

    def play_sound(self):
        """Play the loaded sound."""
        if self.sound:
            self.sound.play()
            self.update_status(
                f"Playing: {self.root.ids.file_input.text}\n"
                f"Provider: {self.root.ids.provider_spinner.text}\n"
                f"Sound class: {self.sound.__class__.__name__}"
            )
        else:
            self.update_status('No sound loaded. Click Load first.')

    def stop_sound(self):
        """Stop the playing sound."""
        if self.sound:
            self.sound.stop()
            self.update_status(
                f"Stopped: {self.root.ids.file_input.text}\n"
                f"Provider: {self.root.ids.provider_spinner.text}"
            )
        else:
            self.update_status('No sound loaded.')

    def update_status(self, message):
        """Update the status label."""
        available = SoundLoader.available_providers()
        self.root.ids.status_label.text = (
            f"{message}\n\n"
            f"Available providers: {', '.join(available)}"
        )

    def on_stop(self):
        """Clean up when app closes."""
        if self.sound:
            self.sound.stop()
            self.sound.unload()


if __name__ == '__main__':
    AudioProviderDemoApp().run()

