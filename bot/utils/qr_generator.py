"""
utils/qr_generator.py
Generate a UPI payment QR code as bytes (PNG).
Returns io.BytesIO — pass directly to aiogram's BufferedInputFile.
"""

import io
import qrcode
from qrcode.image.styledpil import StyledPilImage


def generate_upi_qr(upi_id: str, amount: float, order_id: str, name: str = "Store") -> io.BytesIO:
    """
    Builds a standard UPI deep-link QR code.

    UPI URL format:
        upi://pay?pa=UPI_ID&pn=NAME&am=AMOUNT&tn=NOTE&cu=INR
    """
    upi_url = (
        f"upi://pay"
        f"?pa={upi_id}"
        f"&pn={name.replace(' ', '%20')}"
        f"&am={amount:.2f}"
        f"&tn=Order%20{order_id}"
        f"&cu=INR"
    )

    qr = qrcode.QRCode(
        version=2,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=10,
        border=4,
    )
    qr.add_data(upi_url)
    qr.make(fit=True)

    img = qr.make_image(fill_color="black", back_color="white")

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf
