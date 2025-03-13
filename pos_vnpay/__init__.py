# Part of Odoo. See LICENSE file for full copyright and licensing details.

import base64
import os

from . import controllers
from . import models

from odoo.addons.payment import setup_provider, reset_payment_provider


# Define a function to be called after the module is installed
def post_init_hook(env):
    # Setup the payment provider for "vnpayqr"
    setup_provider(env, "vnpayqr")

    # Get the current module directory
    current_module_directory = os.path.dirname(os.path.abspath(__file__))

    # Get the path to the icon.png file
    icon_path = os.path.join(current_module_directory, "static/description/icon.png")

    # Encode the icon.png file to base64
    with open(icon_path, "rb") as icon_file:
        icon_data = base64.b64encode(icon_file.read()).decode("utf-8")

    # Search for the "vnpay" provider in the "payment.provider" model
    payment_vnpay_qr = env["payment.provider"].search(
        [("code", "=", "vnpayqr")], limit=1
    )
    # If the "vnpayqr" method does not exist, create it
    if not payment_vnpay_qr:
        payment_vnpay_qr = env["payment.provider"].create(
            {
                "name": "VNPay-QR",
                "code": "vnpayqr",
                "image_128": icon_data,
            }
        )

    method_vnpay_qr = env["payment.method"].search([("code", "=", "vnpayqr")], limit=1)
    if not method_vnpay_qr:
        method_vnpay_qr = env["payment.method"].create(
            {
                "code": "vnpayqr",
                "name": "VNPay-QR",
                "sequence": 0,
                "image": icon_data,
                "support_tokenization": False,
                "support_express_checkout": False,
                "support_refund": "partial",
                "active": True,
            }
        )

    payment_vnpay_qr.write(
        {
            "payment_method_ids": [(6, 0, [method_vnpay_qr.id])],
        }
    )

    # Create the VNPay-QR POS payment method if it doesn't exist
    pos_payment_method_vnpay_qr = env["pos.payment.method"].search(
        [("code", "=", "vnpayqr")], limit=1
    )
    if not pos_payment_method_vnpay_qr:
        pos_payment_method_vnpay_qr = env["pos.payment.method"].create(
            {
                "code": "vnpayqr",
                "name": "VNPay-QR",
                "sequence": 0,
                "image": icon_data,
                "is_online_payment": True,
                "has_an_online_payment_provider": True,
                "active": True,
                "type": "online",
                "use_payment_terminal": False,
                "hide_use_payment_terminal": True,
                "outstanding_account_id": False,
                "receivable_account_id": False,
                "online_payment_provider_ids": [(6, 0, [payment_vnpay_qr.id])],
            }
        )


# Define a function to be called when the module is uninstalled
def uninstall_hook(env):
    # Reset the payment provider for "vnpayqr"
    reset_payment_provider(env, "vnpayqr")
