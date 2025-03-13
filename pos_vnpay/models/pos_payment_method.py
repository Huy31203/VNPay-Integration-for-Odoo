import logging

import hmac
import hashlib
import urllib.parse

from odoo import _, api, fields, models
from odoo.addons.payment_vnpay import const
from odoo.addons.payment_vnpay.controllers.main import VNPayController

_logger = logging.getLogger(__name__)


class POSVNPayPaymentMethod(models.Model):
    _inherit = "pos.payment.method"

    code = fields.Char(
        string="Code", help="The technical code of this payment method.", readonly=True
    )
