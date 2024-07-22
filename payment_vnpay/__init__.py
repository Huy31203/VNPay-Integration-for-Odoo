# Part of Odoo. See LICENSE file for full copyright and licensing details.

from . import controllers
from . import models
import logging

from odoo.addons.payment import setup_provider, reset_payment_provider

_logger = logging.getLogger(__name__)


# Define a function to be called after the module is installed
def post_init_hook(env):
    # Setup the payment provider for "vnpay"
    setup_provider(env, "vnpay")
    # Search for the "vnpay" provider in the "payment.provider" model
    payment_vnpay = env["payment.provider"].search([("code", "=", "vnpay")], limit=1)
    # Search for the "vnpay" method in the "payment.method" model
    payment_method_vnpay = env["payment.method"].search(
        [("code", "=", "vnpay")], limit=1
    )

    # Link the found payment method to the found payment provider
    if payment_method_vnpay.id is not False:
        payment_vnpay.write(
            {
                "payment_method_ids": [(6, 0, [payment_method_vnpay.id])],
            }
        )


# Define a function to be called when the module is uninstalled
def uninstall_hook(env):
    # Reset the payment provider for "vnpay"
    reset_payment_provider(env, "vnpay")
