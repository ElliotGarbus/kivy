"""
Test app for Image widget with image_provider property.

Features:
- Dropdown to select image provider
- Button to toggle KIVY_PROVIDER_STRICT mode
- Label showing status (texture loaded or not)
- Image widget using selected provider

Note: The Image widget catches CoreImage exceptions internally and logs them.
Strict mode exceptions won't propagate to the app - check console for warnings/errors.
"""
import os

from kivy.app import App
from kivy.uix.boxlayout import BoxLayout
from kivy.lang import Builder
from kivy.properties import StringProperty, BooleanProperty, ListProperty
from kivy.core.image import Image as CoreImage


KV = '''
<StatusLabel@Label>:
    size_hint_y: 0.1
    text_size: self.size
    halign: 'center'
    valign: 'middle'

<ProviderTestRoot>:
    orientation: 'vertical'
    padding: 10
    spacing: 10
    
    Label:
        text: 'Image Widget Provider Test'
        size_hint_y: 0.08
        font_size: '20sp'
        bold: True
    
    BoxLayout:
        orientation: 'horizontal'
        size_hint_y: 0.12
        spacing: 10
        
        Label:
            text: 'Provider:'
            size_hint_x: 0.15
        
        Spinner:
            id: provider_spinner
            text: '(default)'
            values: root.spinner_values
            size_hint_x: 0.3
            on_text: root.provider_change(self.text)
        
        Button:
            id: strict_btn
            text: 'Strict: OFF'
            size_hint_x: 0.3
            background_color: (0.3, 0.3, 0.3, 1)
            on_press: root.toggle_strict_mode()
    
    StatusLabel:
        id: status_label
        text: 'Check console for status/errors'
        color: (0.7, 0.7, 0.7, 1)
    
    BoxLayout:
        size_hint_y: 0.6
        padding: 20
        
        Image:
            id: test_image
            source: 'kivy/data/logo/kivy-icon-128.png'
            fit_mode: 'contain'
            nocache: True
    
    Label:
        id: info_label
        text: 'Available providers: ' + str(root.providers)
        size_hint_y: 0.1
        font_size: '12sp'
        color: (0.5, 0.5, 0.5, 1)
'''


class ProviderTestRoot(BoxLayout):
    providers = ListProperty([])
    spinner_values = ListProperty([])
    strict_mode = BooleanProperty(False)
    
    def __init__(self, **kwargs):
        # Get available providers before super().__init__
        self.providers = CoreImage.available_providers()
        self.spinner_values = ['(default)', '(invalid_test)'] + self.providers
        super().__init__(**kwargs)
    
    def provider_change(self, provider_text):
        """Handle provider selection change."""
        self.load_with_provider(provider_text)
    
    def load_with_provider(self, provider_text):
        """Load image with the selected provider.
        
        Note: The Image widget catches exceptions internally and logs errors,
        so we check texture presence to determine success/failure.
        """
        if provider_text == '(default)':
            provider = None
        elif provider_text == '(invalid_test)':
            provider = 'nonexistent_provider_xyz'
        else:
            provider = provider_text
        
        test_image = self.ids.test_image
        test_image.image_provider = provider
        
    def toggle_strict_mode(self):
        """Toggle KIVY_PROVIDER_STRICT environment variable."""
        self.strict_mode = not self.strict_mode
        btn = self.ids.strict_btn
        
        if self.strict_mode:
            os.environ['KIVY_PROVIDER_STRICT'] = '1'
            btn.text = 'Strict: ON'
            btn.background_color = (0.8, 0.2, 0.2, 1)
            self.set_status('Strict mode ENABLED - errors raise exceptions, check console for errors')
        else:
            os.environ['KIVY_PROVIDER_STRICT'] = '0'
            btn.text = 'Strict: OFF'
            btn.background_color = (0.3, 0.3, 0.3, 1)
            self.set_status('Strict mode DISABLED - errors fallback, check console for warnings')
    
    def set_status(self, text, color=(0.7, 0.7, 0.7, 1)):
        """Update status label."""
        self.ids.status_label.text = text
        self.ids.status_label.color = color
        print(f"Status: {text}")


class ProviderTestApp(App):
    def build(self):
        Builder.load_string(KV)
        return ProviderTestRoot()


if __name__ == '__main__':
    ProviderTestApp().run()
