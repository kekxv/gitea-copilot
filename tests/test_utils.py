import pytest
from app.utils.qrcode import generate_qr_code_data_uri
import io
from PIL import Image

def test_generate_qr_code_data_uri():
    data = "otpauth://totp/GiteaCopilot:test_user?secret=JBSWY3DPEHPK3PXP&issuer=GiteaCopilot"
    
    qr_base64 = generate_qr_code_data_uri(data)
    
    assert qr_base64.startswith("data:image/png;base64,")
    # Should be valid base64
    import base64
    img_data = base64.b64decode(qr_base64.split(",")[1])
    img = Image.open(io.BytesIO(img_data))
    assert img.format == "PNG"
