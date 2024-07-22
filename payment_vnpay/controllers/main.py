# Part of Odoo. See LICENSE file for full copyright and licensing details.

import hashlib
import hmac
import logging
import pprint
import urllib.parse

from werkzeug.exceptions import Forbidden
from odoo import _, http
from odoo.exceptions import ValidationError
from odoo.http import request


_logger = logging.getLogger(__name__)


class VNPayController(http.Controller):
    _return_url = "/payment/vnpay/return"
    # Get the IPN URL from the payment provider configuration.
    _ipn_url = "/payment/vnpay/webhook"

    @http.route(
        _return_url,
        type="http",
        methods=["GET"],
        auth="public",
        csrf=False,
        saveSession=False,  # No need to save the session
    )
    def vnpay_return_from_checkout(self, **data):
        """No need to handle the data from the return URL because the IPN already handled it."""

        _logger.info("Handling redirection from VNPay.")

        # Redirect user to the status page.
        # After redirection, user will see the payment status once the IPN processing is complete.
        return request.redirect("/payment/status")

    @http.route(
        _ipn_url,
        type="http",
        auth="public",
        methods=["GET"],
        csrf=False,
        saveSession=False,  # No need to save the session
    )
    def vnpay_webhook(self, **data):
        """Process the notification data (IPN) sent by VNPay to the webhook.

        The "Instant Payment Notification" is a classical webhook notification.

        :param dict data: The notification data
        :return: The response to give to VNPay and acknowledge the notification
        """

        ip_address = request.httprequest.environ.get("REMOTE_ADDR")
        _logger.info(
            "notification received from VNPay with data:\n%s\nFrom IP: %s",
            pprint.pformat(data),
            ip_address,
        )

        white_list_ip = (
            http.request.env["payment_provider"]
            .search([("code", "=", "vnpay")], limit=1)
            .vnpay_white_list_ip
        )

        # Convert the white list IP to a list of IPs.
        white_list_ip = white_list_ip = white_list_ip.replace(" ", "").split(";")

        if ip_address not in white_list_ip:
            _logger.warning(
                "Received notification from an unauthorized IP address: %s", ip_address
            )
            # Not handling the unauthorized notification data.
            return

        try:
            tx_sudo = (
                request.env["payment.transaction"]
                .sudo()
                ._get_tx_from_notification_data("vnpay", data)
            )
            # Verify the signature of the notification data.
            self._verify_notification_signature(data, tx_sudo)

            # Handle the notification data
            tx_sudo._handle_notification_data("vnpay", data)
        except Forbidden:
            _logger.warning(
                "Forbidden error during signature verification. Aborting.",
                exc_info=True,
            )
            tx_sudo._set_error("VNPay: " + _("Received data with invalid signature."))
            # Return VNPAY: Invalid Signature
            return request.make_json_response(
                {"RspCode": "97", "Message": "Invalid Checksum"}
            )

        except AssertionError:
            _logger.warning(
                "Assertion error during notification handling. Aborting.",
                exc_info=True,
            )
            tx_sudo._set_error("VNPay: " + _("Received data with invalid amount."))
            # Return VNPAY: Invalid amount
            return request.make_json_response(
                {"RspCode": "04", "Message": "invalid amount"}
            )

        except ValidationError:
            _logger.warning(
                "Unable to handle the notification data. Aborting.",
                exc_info=True,
            )
            # Return VNPAY: Order Not Found
            return request.make_json_response(
                {"RspCode": "01", "Message": "Order Not Found"}
            )

        # Check if the transaction has already been processed.
        if tx_sudo.state in ["done", "cancel", "error"]:
            _logger.warning(
                "Received notification for already processed transaction. Aborting."
            )
            # Return VNPAY: Already update
            return request.make_json_response(
                {"RspCode": "02", "Message": "Order already confirmed"}
            )

        responseCode = data.get("vnp_ResponseCode")

        if responseCode == "00":
            # Confirm the transaction if the payment was successful.
            _logger.info("Received successful payment notification from VNPay, saving.")
            tx_sudo._set_done()
            _logger.info("Payment transaction completed.")
        elif responseCode == "24":
            # Cancel the transaction if the payment was canceled by the user.
            _logger.warning(
                "Received canceled payment notification from VNPay, canceling."
            )
            tx_sudo._set_canceled(state_message=_("The customer canceled the payment."))
            _logger.info("Payment transaction canceled.")
        else:
            # Notify the user that the payment failed.
            _logger.warning(
                "Received payment notification from VNPay with invalid response code: %s",
                responseCode,
            )
            tx_sudo._set_error(
                "VNPay: "
                + _("Received data with invalid response code: %s", responseCode)
            )
            _logger.info("Payment transaction failed.")
        # Return VNPAY: Merchant update success
        return request.make_json_response(
            {"RspCode": "00", "Message": "Confirm Success"}
        )

    @staticmethod
    def _verify_notification_signature(data, tx_sudo):
        """Check that the received signature matches the expected one.
        * The signature in the payment link and the signature in the notification data are different.

        :param dict received_signature: The signature received with the notification data.
        :param recordset tx_sudo: The sudoed transaction referenced by the notification data, as a
                                    `payment.transaction` record.

        :return: None
        :raise Forbidden: If the signatures don't match.
        """
        # Check if data is empty.
        if not data:
            _logger.warning("Received notification with missing data.")
            raise Forbidden()

        receive_signature = data.get("vnp_SecureHash")

        # Remove the signature from the data to verify.
        if data.get("vnp_SecureHash"):
            data.pop("vnp_SecureHash")
        if data.get("vnp_SecureHashType"):
            data.pop("vnp_SecureHashType")

        # Sort the data by key to generate the expected signature.
        inputData = sorted(data.items())
        hasData = ""
        seq = 0
        for key, val in inputData:
            if str(key).startswith("vnp_"):
                if seq == 1:
                    hasData = (
                        hasData
                        + "&"
                        + str(key)
                        + "="
                        + urllib.parse.quote_plus(str(val))
                    )
                else:
                    seq = 1
                    hasData = str(key) + "=" + urllib.parse.quote_plus(str(val))

        # Generate the expected signature.
        expected_signature = VNPayController.__hmacsha512(
            tx_sudo.provider_id.vnpay_hash_secret, hasData
        )

        # Compare the received signature with the expected signature.
        if not hmac.compare_digest(receive_signature, expected_signature):
            _logger.warning("Received notification with invalid signature.")
            raise Forbidden()

    @staticmethod
    def __hmacsha512(key, data):
        """Generate a HMAC SHA512 hash"""

        byteKey = key.encode("utf-8")
        byteData = data.encode("utf-8")
        return hmac.new(byteKey, byteData, hashlib.sha512).hexdigest()
