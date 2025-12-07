"""
Test script for per-instance image provider selection

Note: must set nocache=True to avoid hitting cached image from previous tests
"""
import os
import zipfile
import tempfile
import shutil
from pathlib import Path


# Uncomment to test with limited providers
# os.environ['KIVY_IMAGE'] = 'pil,sdl3'


from kivy.core.image import Image as CoreImage

# Get a test image path (use any image you have)
TEST_IMAGE = 'kivy/data/logo/kivy-icon-128.png'

# Test images for zip file
TEST_IMAGES_FOR_ZIP = [
    Path('kivy/data/logo/kivy-icon-128.png'),
    Path('kivy/data/logo/kivy-icon-64.png'),
    Path('kivy/data/logo/kivy-icon-32.png'),
]


def create_test_zip():
    """Create a temporary zip file with test images."""
    # Create a temporary directory
    temp_dir = Path(tempfile.mkdtemp())
    zip_path = temp_dir / 'test_images.zip'
    
    with zipfile.ZipFile(zip_path, 'w') as zf:
        for img_path in TEST_IMAGES_FOR_ZIP:
            if img_path.exists():
                # Add with just the filename, not full path
                zf.write(str(img_path), img_path.name)
    
    return zip_path, temp_dir


def test_available_providers():
    """Test querying available providers."""
    print("=" * 60)
    print("TEST: available_providers()")
    print("=" * 60)
    providers = CoreImage.available_providers()
    print(f"Available providers: {providers}")
    print()


def test_default_provider():
    """Test loading with default provider selection."""
    print("=" * 60)
    print("TEST: Default provider (no image_provider specified)")
    print("=" * 60)
    try:
        img = CoreImage.load(TEST_IMAGE, nocache=True)
        print(f"SUCCESS: Loaded {TEST_IMAGE}")
        print(f"  Size: {img.size}")
    except Exception as e:
        print(f"FAILED: {e}")
    print()


def test_specific_provider():
    """Test loading with a specific valid provider."""
    print("=" * 60)
    print("TEST: Specific valid provider")
    print("=" * 60)
    providers = CoreImage.available_providers()
    if providers:
        provider = providers[0]
        print(f"Using provider: {provider}")
        try:
            # Use nocache=True to ensure we actually use the specified provider
            img = CoreImage.load(TEST_IMAGE, image_provider=provider, nocache=True)
            print(f"SUCCESS: Loaded with {provider}")
            print(f"  Size: {img.size}")
        except Exception as e:
            print(f"FAILED: {e}")
    else:
        print("No providers available!")
    print()


def test_invalid_provider():
    """Test loading with an invalid provider name (typo)."""
    print("=" * 60)
    print("TEST: Invalid provider name (should warn or raise ValueError)")
    print("=" * 60)
    try:
        # Use nocache=True to avoid hitting cached image from previous tests
        img = CoreImage.load(TEST_IMAGE, image_provider='invalid_provider_xyz', nocache=True)
        if img:
            print(f"FALLBACK SUCCESS: Loaded despite invalid provider")
            print(f"  Size: {img.size}")
        else:
            print("RESULT: Returned None (all fallbacks failed)")
    except ValueError as e:
        print(f"STRICT MODE - ValueError raised: {e}")
    except Exception as e:
        print(f"FAILED with unexpected error: {type(e).__name__}: {e}")
    print()


def test_provider_wrong_format():
    """Test loading with a provider that doesn't support the format."""
    print("=" * 60)
    print("TEST: Provider that doesn't support format (e.g., 'tex' for PNG)")
    print("=" * 60)
    # 'tex' loader only supports .tex files, not PNG
    if 'tex' in CoreImage.available_providers():
        try:
            # Use nocache=True to avoid hitting cached image
            img = CoreImage.load(TEST_IMAGE, image_provider='tex', nocache=True)
            if img:
                print(f"FALLBACK SUCCESS: Loaded despite format mismatch")
                print(f"  Size: {img.size}")
            else:
                print("RESULT: Returned None (all fallbacks failed)")
        except ValueError as e:
            print(f"STRICT MODE - ValueError raised: {e}")
        except Exception as e:
            print(f"FAILED with unexpected error: {type(e).__name__}: {e}")
    else:
        print("SKIPPED: 'tex' provider not available")
    print()


def test_strict_mode_demo():
    """Demonstrate strict mode behavior."""
    print("=" * 60)
    print("TEST: Strict mode status")
    print("=" * 60)
    strict = os.environ.get('KIVY_PROVIDER_STRICT', '').lower() in ('1', 'true', 'yes')
    print(f"KIVY_PROVIDER_STRICT is: {'ON' if strict else 'OFF (default)'}")
    if not strict:
        print("  In strict mode, invalid providers raise exceptions instead of warnings")
    print()


def test_provider_uri_scheme():
    """Test @image_provider:providername(path) URI scheme."""
    print("=" * 60)
    print("TEST: Provider URI scheme (@image_provider:provider(path))")
    print("=" * 60)
    providers = CoreImage.available_providers()
    if providers:
        provider = providers[0]
        uri = f"@image_provider:{provider}({TEST_IMAGE})"
        print(f"Using URI: {uri}")
        try:
            img = CoreImage.load(uri, nocache=True)
            print(f"SUCCESS: Loaded with provider URI")
            print(f"  Size: {img.size}")
        except Exception as e:
            print(f"FAILED: {type(e).__name__}: {e}")
    else:
        print("No providers available!")
    print()


def test_provider_uri_pil():
    """Test @image_provider URI with PIL provider (expected to succeed)."""
    print("=" * 60)
    print("TEST: Provider URI with 'pil' provider (should succeed)")
    print("=" * 60)
    if 'pil' not in CoreImage.available_providers():
        print("SKIPPED: 'pil' provider not available")
        print()
        return
    
    uri = f"@image_provider:pil({TEST_IMAGE})"
    print(f"Using URI: {uri}")
    try:
        img = CoreImage.load(uri, nocache=True)
        print(f"SUCCESS: Loaded with PIL via URI")
        print(f"  Size: {img.size}")
        assert img.size == (128, 128), f"Unexpected size: {img.size}"
        print("  PASS: Size matches expected (128, 128)")
    except Exception as e:
        print(f"FAILED: {type(e).__name__}: {e}")
    print()


def test_provider_uri_invalid():
    """Test @image_provider URI with invalid provider."""
    print("=" * 60)
    print("TEST: Provider URI with invalid provider name")
    print("=" * 60)
    uri = f"@image_provider:invalid_xyz({TEST_IMAGE})"
    print(f"Using URI: {uri}")
    try:
        img = CoreImage.load(uri, nocache=True)
        if img:
            print(f"FALLBACK SUCCESS: Loaded despite invalid provider in URI")
            print(f"  Size: {img.size}")
        else:
            print("RESULT: Returned None")
    except ValueError as e:
        print(f"STRICT MODE - ValueError raised: {e}")
    except Exception as e:
        print(f"FAILED: {type(e).__name__}: {e}")
    print()


def test_zip_with_provider():
    """Test loading a zip file with image_provider parameter."""
    print("=" * 60)
    print("TEST: Load zip file with image_provider parameter")
    print("=" * 60)
    
    # Create temporary zip file
    zip_path, temp_dir = create_test_zip()
    print(f"Created test zip: {zip_path}")
    
    try:
        providers = CoreImage.available_providers()
        if not providers:
            print("SKIPPED: No providers available")
            return
        
        provider = providers[-1]
        print(f"Using provider: {provider}")
        
        # Load zip file with specific provider (convert Path to str)
        img = CoreImage.load(str(zip_path), image_provider=provider, nocache=True)
        print(f"SUCCESS: Loaded zip file with {provider}")
        print(f"  Size: {img.size}")
        print(f"  Number of frames: {len(img.image._data) if img.image else 'N/A'}")
        
    except Exception as e:
        print(f"FAILED: {type(e).__name__}: {e}")
    finally:
        # Clean up temporary directory
        shutil.rmtree(temp_dir)
        print(f"Cleaned up temp dir: {temp_dir}")
    print()


if __name__ == '__main__':
    for strict in ['0', '1']:
        os.environ['KIVY_PROVIDER_STRICT'] = strict
        test_strict_mode_demo()
        test_available_providers()
        test_default_provider()
        test_specific_provider()
        test_invalid_provider()
        test_provider_wrong_format()
        test_provider_uri_scheme()
        test_provider_uri_pil()
        test_provider_uri_invalid()
        test_zip_with_provider()

        print("=" * 60)
        print("All tests completed!")
        print("=" * 60)
