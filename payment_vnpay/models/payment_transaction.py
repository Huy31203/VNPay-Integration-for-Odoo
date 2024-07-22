# Part of Odoo. See LICENSE file for full copyright and licensing details.

import logging
import pytz
import re
import unicodedata

from werkzeug import urls
from datetime import datetime, timedelta

from odoo import _, api, models
from odoo.exceptions import ValidationError

from odoo.addons.payment import utils as payment_utils
from odoo.addons.payment_vnpay.controllers.main import VNPayController

_logger = logging.getLogger(__name__)


class PaymentTransaction(models.Model):
    _inherit = "payment.transaction"

    def _get_specific_rendering_values(self, processing_values):
        """Override of payment to return VNPay-specific rendering values.

        Note: self.ensure_one() from `_get_processing_values`

        :param dict processing_values: The generic and specific processing values of the transaction
        :return: The dict of provider-specific processing values.
        :rtype: dict
        """
        res = super()._get_specific_rendering_values(processing_values)
        if self.provider_code != "vnpay":
            return res

        # Initiate the payment and retrieve the payment link data.
        base_url = self.provider_id.get_base_url()

        # Determine the language of the payment page.
        language = (
            "vn"
            if self.env.context.get("lang", self.env.user.lang) == "vi_VN"
            else "en"
        )
        float_amount = round(float(self.amount), 2)

        params = {
            "vnp_Version": "2.1.1",
            "vnp_Command": "pay",
            "vnp_TmnCode": self.provider_id.vnpay_tmn_code,
            "vnp_Amount": int(float_amount * 100),
            "vnp_CreateDate": datetime.now(pytz.timezone("Etc/GMT-7")).strftime(
                "%Y%m%d%H%M%S"
            ),
            "vnp_CurrCode": "VND",
            "vnp_IpAddr": payment_utils.get_customer_ip_address(),
            "vnp_Locale": language,
            "vnp_OrderInfo": f"Thanh toan don hang {self.reference} voi so tien la {float_amount} VND",
            "vnp_OrderType": "billpayment",
            "vnp_ReturnUrl": urls.url_join(base_url, VNPayController._return_url),
            "vnp_ExpireDate": (
                datetime.now(pytz.timezone("Etc/GMT-7")) + timedelta(minutes=30)
            ).strftime("%Y%m%d%H%M%S"),
            "vnp_TxnRef": self.reference,
        }

        payment_link_data = self.provider_id._get_payment_url(
            params=params, secret_key=self.provider_id.vnpay_hash_secret
        )

        # Extract the payment link URL and embed it in the redirect form.
        rendering_values = {
            "api_url": payment_link_data,
        }
        return rendering_values

    def _get_tx_from_notification_data(self, provider_code, notification_data):
        """Override of payment to find the transaction based on VNPay data.

        :param str provider_code: The code of the provider that handled the transaction.
        :param dict notification_data: The notification data sent by the provider.
        :return: The transaction if found.
        :rtype: recordset of `payment.transaction`
        :raise ValidationError: If inconsistent data were received.
        :raise ValidationError: If the data match no transaction.
        """
        tx = super()._get_tx_from_notification_data(provider_code, notification_data)
        if provider_code != "vnpay" or len(tx) == 1:
            return tx

        reference = notification_data.get("vnp_TxnRef")
        if not reference:
            raise ValidationError(
                "VNPay: " + _("Received data with missing reference.")
            )

        tx = self.search(
            [("reference", "=", reference), ("provider_code", "=", "vnpay")]
        )
        if not tx:
            raise ValidationError(
                "VNPay: " + _("No transaction found matching reference %s.", reference)
            )
        return tx

    def _process_notification_data(self, notification_data):
        """Override of payment to process the transaction based on VNPay data.

        Note: self.ensure_one()

        :param dict notification_data: The notification data sent by the provider.
        :return: None
        :raise ValidationError: If inconsistent data were received.
        """
        super()._process_notification_data(notification_data)
        if self.provider_code != "vnpay":
            return

        if not notification_data:
            self._set_canceled(state_message=_("The customer left the payment page."))
            return

        amount = notification_data.get("vnp_Amount")
        assert amount, "VNPay: missing amount"
        assert (
            self.currency_id.compare_amounts(float(amount) / 100, self.amount) == 0
        ), "VNPay: mismatching amounts"

        vnp_txn_ref = notification_data.get("vnp_TxnRef")

        if not vnp_txn_ref:
            raise ValidationError(
                "VNPay: " + _("Received data with missing reference.")
            )
        self.provider_reference = vnp_txn_ref

    # Override the _compute_reference and replace the separator with 'c'
    @api.model
    def _compute_reference(self, provider_code, prefix=None, separator="c", **kwargs):
        """Compute a unique reference for the transaction.

        The reference corresponds to the prefix if no other transaction with that prefix already
        exists. Otherwise, it follows the pattern `{computed_prefix}{separator}{sequence_number}`
        where:

        - `{computed_prefix}` is:

        - The provided custom prefix, if any.
        - The computation result of :meth:`_compute_reference_prefix` if the custom prefix is not
            filled, but the kwargs are.
        - `'tx-{datetime}'` if neither the custom prefix nor the kwargs are filled.

        - `{separator}` is the string that separates the prefix from the sequence number.
        - `{sequence_number}` is the next integer in the sequence of references sharing the same
        prefix. The sequence starts with `1` if there is only one matching reference.

        .. example::

        - Given the custom prefix `'example'` which has no match with an existing reference, the
            full reference will be `'example'`.
        - Given the custom prefix `'example'` which matches the existing reference `'example'`,
            and the custom separator `'-'`, the full reference will be `'example-1'`.
        - Given the kwargs `{'invoice_ids': [1, 2]}`, the custom separator `'-'` and no custom
            prefix, the full reference will be `'INV1-INV2'` (or similar) if no existing reference
            has the same prefix, or `'INV1-INV2-n'` if `n` existing references have the same
            prefix.

        :param str provider_code: The code of the provider handling the transaction.
        :param str prefix: The custom prefix used to compute the full reference.
        :param str separator: The custom separator used to separate the prefix from the suffix.
        :param dict kwargs: Optional values passed to :meth:`_compute_reference_prefix` if no custom
                            prefix is provided.
        :return: The unique reference for the transaction.
        :rtype: str
        """
        # Compute the prefix.
        if prefix:
            # Replace special characters by their ASCII alternative (é -> e ; ä -> a ; ...)
            prefix = (
                unicodedata.normalize("NFKD", prefix)
                .encode("ascii", "ignore")
                .decode("utf-8")
            )
        if (
            not prefix
        ):  # Prefix not provided or voided above, compute it based on the kwargs.
            prefix = self.sudo()._compute_reference_prefix(
                provider_code, separator, **kwargs
            )
        if (
            not prefix
        ):  # Prefix not computed from the kwargs, fallback on time-based value
            prefix = payment_utils.singularize_reference_prefix()

        # Compute the sequence number.
        reference = prefix  # The first reference of a sequence has no sequence number.
        if self.sudo().search(
            [("reference", "=", prefix)]
        ):  # The reference already has a match
            # We now execute a second search on `payment.transaction` to fetch all the references
            # starting with the given prefix. The load of these two searches is mitigated by the
            # index on `reference`. Although not ideal, this solution allows for quickly knowing
            # whether the sequence for a given prefix is already started or not, usually not. An SQL
            # query wouldn't help either as the selector is arbitrary and doing that would be an
            # open-door to SQL injections.
            same_prefix_references = (
                self.sudo()
                .search([("reference", "=like", f"{prefix}{separator}%")])
                .with_context(prefetch_fields=False)
                .mapped("reference")
            )

            # A final regex search is necessary to figure out the next sequence number. The previous
            # search could not rely on alphabetically sorting the reference to infer the largest
            # sequence number because both the prefix and the separator are arbitrary. A given
            # prefix could happen to be a substring of the reference from a different sequence.
            # For instance, the prefix 'example' is a valid match for the existing references
            # 'example', 'example-1' and 'example-ref', in that order. Trusting the order to infer
            # the sequence number would lead to a collision with 'example-1'.
            search_pattern = re.compile(rf"^{re.escape(prefix)}{separator}(\d+)$")
            max_sequence_number = (
                0  # If no match is found, start the sequence with this reference.
            )
            for existing_reference in same_prefix_references:
                search_result = re.search(search_pattern, existing_reference)
                if (
                    search_result
                ):  # The reference has the same prefix and is from the same sequence
                    # Find the largest sequence number, if any.
                    current_sequence = int(search_result.group(1))
                    if current_sequence > max_sequence_number:
                        max_sequence_number = current_sequence

            # Compute the full reference.
            reference = f"{prefix}{separator}{max_sequence_number + 1}"
        return reference
