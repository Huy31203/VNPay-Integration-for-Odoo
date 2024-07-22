import logging

import hmac
import hashlib
import urllib.parse

from odoo import _, api, fields, models
from odoo.addons.payment_vnpay import const
from odoo.addons.payment_vnpay.controllers.main import VNPayController

_logger = logging.getLogger(__name__)


class PaymentProviderVNPay(models.Model):
    _inherit = "payment.provider"

    @api.model
    def _get_default_vnpay_ipn_url(self):
        base_url = self.env["ir.config_parameter"].sudo().get_param("web.base.url")
        return base_url + VNPayController._ipn_url

    # Add 'VNPay' as a new payment provider
    code = fields.Selection(
        selection_add=[("vnpay", "VNPay")], ondelete={"vnpay": "set default"}
    )

    # Define fields for VNPay's Tmn Code and Hash Secret
    vnpay_tmn_code = fields.Char(
        string="VNPay Website Code (TmnCode)", required_if_provider="vnpay"
    )
    vnpay_hash_secret = fields.Char(
        string="VNPay Hash Secret (vnp_HashSecret)", required_if_provider="vnpay"
    )

    vnpay_payment_link = fields.Char(
        string="VNPay Payment URL (vnp_Url)", required_if_provider="vnpay"
    )

    vnpay_white_list_ip = fields.Char(
        string="VNPay White List IPs",
        required_if_provider="vnpay",
        default="113.160.92.202; 113.52.45.78; 116.97.245.130; 42.118.107.252; 113.20.97.250; 203.171.19.146; 103.220.87.4; 103.220.86.4",
    )

    # get the base url and pass it into defaut value of vnpay_ipn_url
    vnpay_ipn_url = fields.Char(
        string="VNPay IPN URL",
        required_if_provider="vnpay",
        default=_get_default_vnpay_ipn_url,
    )

    @api.model
    def _get_compatible_providers(
        self, *args, currency_id=None, is_validation=False, **kwargs
    ):
        """Override of payment to filter out VNPay providers for unsupported currencies or
        for validation operations."""
        providers = super()._get_compatible_providers(
            *args, currency_id=currency_id, is_validation=is_validation, **kwargs
        )

        currency = self.env["res.currency"].browse(currency_id).exists()
        # Filter out VNPay if the currency is not supported or if it's a validation operation
        if (
            currency and currency.name not in const.SUPPORTED_CURRENCIES
        ) or is_validation:
            providers = providers.filtered(lambda p: p.code != "vnpay")

        return providers

    def _get_supported_currencies(self):
        """Override of `payment` to return the supported currencies."""

        supported_currencies = super()._get_supported_currencies()
        if self.code == "vnpay":
            supported_currencies = supported_currencies.filtered(
                lambda c: c.name in const.SUPPORTED_CURRENCIES
            )
        return supported_currencies

    def _get_payment_url(self, params, secret_key):
        """Generate the payment URL for VNPay"""

        # Determine the base URL based on the state
        inputData = sorted(params.items())
        queryString = ""
        seq = 0
        for key, val in inputData:
            if seq == 1:
                queryString = (
                    queryString + "&" + key + "=" + urllib.parse.quote_plus(str(val))
                )
            else:
                seq = 1
                queryString = key + "=" + urllib.parse.quote_plus(str(val))

        hashValue = self.__hmacsha512(secret_key, queryString)
        # The final URL will be like this:
        # base_url?param1=value1&param2=value2...&vnp_SecureHash=hashValue
        return (
            self.vnpay_payment_link + "?" + queryString + "&vnp_SecureHash=" + hashValue
        )

    def _get_default_payment_method_codes(self):
        """Override of `payment` to return the default payment method codes."""
        default_codes = super()._get_default_payment_method_codes()
        if self.code != "vnpay":
            return default_codes
        return const.DEFAULT_PAYMENT_METHODS_CODES

    @staticmethod
    def __hmacsha512(key, data):
        """Generate a HMAC SHA512 hash"""

        byteKey = key.encode("utf-8")
        byteData = data.encode("utf-8")
        return hmac.new(byteKey, byteData, hashlib.sha512).hexdigest()
