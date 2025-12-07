"""
Demo app showing @image_provider URI scheme with graphics instructions in KV.

This demonstrates using the provider URI to specify which image provider
to use when loading textures for Rectangle and other canvas instructions.
"""
from kivy.app import App
from kivy.uix.widget import Widget
from kivy.lang import Builder
from kivy.core.image import Image as CoreImage


# Detect available provider for the demo
def get_provider():
    providers = CoreImage.available_providers()
    return providers[-1] # pil


PROVIDER = get_provider()
IMAGE_PATH = 'kivy/data/logo/kivy-icon-128.png'

# Build the KV string with the provider URI
KV = f'''
<ProviderURIDemo>:
    canvas:
        Color:
            rgba: 0.15, 0.15, 0.2, 1
        Rectangle:
            pos: self.pos
            size: self.size
        Color:
            rgba: 1, 1, 1, 1
        Rectangle:
            source: f'@image_provider:{PROVIDER}({IMAGE_PATH})'
            pos: self.center_x - 64, self.center_y - 64
            size: 128, 128

BoxLayout:
    orientation: 'vertical'
    
    Label:
        size_hint_y: 0.15
        text: 'Provider URI Demo'
        font_size: '24sp'
        bold: True
    
    Label:
        size_hint_y: 0.1
        text: "Available: " + str({CoreImage.available_providers()})
        font_size: '14sp'
    
    Label:
        size_hint_y: 0.1
        text: f"Using: @image_provider:{PROVIDER}({IMAGE_PATH})"
        font_size: '14sp'
        color: 0.5, 1, 0.5, 1
    
    ProviderURIDemo:
        size_hint_y: 0.65
'''


class ProviderURIDemo(Widget):
    pass


class ProviderURIApp(App):
    def build(self):
        print(f"Available providers: {CoreImage.available_providers()}")
        print(f"Using provider: {PROVIDER}")
        print(f"Provider URI: @image_provider:{PROVIDER}({IMAGE_PATH})")
        return Builder.load_string(KV)


if __name__ == '__main__':
    ProviderURIApp().run()
