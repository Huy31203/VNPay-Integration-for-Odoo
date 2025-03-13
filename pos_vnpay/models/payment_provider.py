import logging

import hmac
import hashlib
import urllib.parse

from odoo import _, api, fields, models
from odoo.addons.pos_vnpay import const

from odoo.addons.pos_vnpay.controllers.main import PaymentVNPayPortal

_logger = logging.getLogger(__name__)


class PaymentPOSVNPay(models.Model):
    _inherit = "payment.provider"

    @api.model
    def _get_default_vnpay_pos_ipn_url(self):
        base_url = self.env["ir.config_parameter"].sudo().get_param("web.base.url")
        return base_url + PaymentVNPayPortal._pos_ipn_url

    # Add 'VNPay-QR' as a new payment provider
    code = fields.Selection(
        selection_add=[("vnpayqr", "VNPay-QR")], ondelete={"vnpayqr": "set default"}
    )

    # Define the fields for the VNPay-QR
    vnpayqr_tmn_code = fields.Char(
        string="VNPay-QR Website Code for QR (TmnCode)", required_if_provider="vnpayqr"
    )

    vnpayqr_merchant_code = fields.Char(
        string="VNPay-QR Merchant Code", required_if_provider="vnpayqr"
    )
    vnpayqr_merchant_name = fields.Char(
        string="VNPay-QR Merchant Name", required_if_provider="vnpayqr"
    )

    vnpayqr_merchant_type = fields.Char(
        string="VNPay-QR Merchant Type", required_if_provider="vnpayqr"
    )

    vnpayqr_app_id = fields.Char(
        string="VNPay-QR App ID", required_if_provider="vnpayqr"
    )

    vnpayqr_secret_key = fields.Char(
        string="VNPay-QR secret key", required_if_provider="vnpayqr"
    )

    vnpayqr_create_url = fields.Char(
        string="VNPay-QR create URL", required_if_provider="vnpayqr"
    )

    # get the base url and pass it into defaut value of vnpay_ipn_url
    vnpayqr_ipn_url = fields.Char(
        string="VNPay-QR IPN URL",
        required_if_provider="vnpayqr",
        default=_get_default_vnpay_pos_ipn_url,
    )

    @api.model
    def _get_compatible_providers(
        self, *args, currency_id=None, is_validation=False, **kwargs
    ):
        """Override of payment to filter out VNPay-QR because it only support POS, not ecommerce."""
        providers = super()._get_compatible_providers(
            *args, currency_id=currency_id, is_validation=is_validation, **kwargs
        )

        providers = providers.filtered(lambda p: p.code != "vnpayqr")

        return providers

    def _get_supported_currencies(self):
        """Override of `payment` to return the supported currencies."""

        supported_currencies = super()._get_supported_currencies()
        if self.code == "vnpayqr":
            supported_currencies = supported_currencies.filtered(
                lambda c: c.name in const.SUPPORTED_CURRENCIES
            )
        return supported_currencies

    def _get_default_payment_method_codes(self):
        """Override of `payment` to return the default payment method codes."""
        default_codes = super()._get_default_payment_method_codes()
        if self.code != "vnpayqr":
            return default_codes
        return const.DEFAULT_PAYMENT_METHODS_CODES
