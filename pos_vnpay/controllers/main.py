# Part of Odoo. See LICENSE file for full copyright and licensing details.

import hmac
import hashlib
import logging
import qrcode
import pytz
import base64
import requests as pyreq
import json

from io import BytesIO
from decimal import *
from werkzeug.urls import url_encode
from datetime import datetime, timedelta
from werkzeug.exceptions import Forbidden

from odoo import http, _, tools
from odoo.exceptions import AccessError, ValidationError, UserError
from odoo.addons.payment.controllers.post_processing import PaymentPostProcessing
from odoo.addons.payment.controllers import portal as payment_portal
from odoo.http import request


_logger = logging.getLogger(__name__)


class PaymentVNPayPortal(payment_portal.PaymentPortal):
    _create_qr_url = "/pos/vnpay/get_payment_qr"
    _pos_ipn_url = "/pos/vnpay/webhook"

    # Only override to change the prefix of the reference
    def _create_transaction(
        self,
        provider_id,
        payment_method_id,
        token_id,
        amount,
        currency_id,
        partner_id,
        flow,
        tokenization_requested,
        landing_route,
        reference_prefix=None,
        is_validation=False,
        custom_create_values=None,
        **kwargs,
    ):
        """Override the original method to create a transaction for the POS VNPay payment method."""
        # Prepare create values
        if flow in ["redirect", "direct"]:  # Direct payment or payment with redirection
            provider_sudo = request.env["payment.provider"].sudo().browse(provider_id)
            token_id = None
            tokenize = bool(
                # Don't tokenize if the user tried to force it through the browser's developer tools
                provider_sudo.allow_tokenization
                # Token is only created if required by the flow or requested by the user
                and (
                    provider_sudo._is_tokenization_required(**kwargs)
                    or tokenization_requested
                )
            )
        elif flow == "token":  # Payment by token
            token_sudo = request.env["payment.token"].sudo().browse(token_id)

            # Prevent from paying with a token that doesn't belong to the current partner (either
            # the current user's partner if logged in, or the partner on behalf of whom the payment
            # is being made).
            partner_sudo = request.env["res.partner"].sudo().browse(partner_id)
            if (
                partner_sudo.commercial_partner_id
                != token_sudo.partner_id.commercial_partner_id
            ):
                raise AccessError(_("You do not have access to this payment token."))

            provider_sudo = token_sudo.provider_id
            payment_method_id = token_sudo.payment_method_id.id
            tokenize = False
        else:
            raise ValidationError(
                _(
                    "The payment should either be direct, with redirection, or made by a token."
                )
            )

        reference = request.env["payment.transaction"]._compute_reference(
            provider_sudo.code,
            prefix=reference_prefix,
            separator="-",
            **(custom_create_values or {}),
            **kwargs,
        )
        if (
            is_validation
        ):  # Providers determine the amount and currency in validation operations
            amount = provider_sudo._get_validation_amount()
            payment_method = request.env["payment.method"].browse(payment_method_id)
            currency_id = (
                provider_sudo.with_context(
                    validation_pm=payment_method  # Will be converted to a kwarg in master.
                )
                ._get_validation_currency()
                .id
            )

        # Create the transaction
        tx_sudo = (
            request.env["payment.transaction"]
            .sudo()
            .create(
                {
                    "provider_id": provider_sudo.id,
                    "payment_method_id": payment_method_id,
                    "reference": reference,
                    "amount": amount,
                    "currency_id": currency_id,
                    "partner_id": partner_id,
                    "token_id": token_id,
                    "operation": (
                        f"online_{flow}" if not is_validation else "validation"
                    ),
                    "tokenize": tokenize,
                    "landing_route": landing_route,
                    **(custom_create_values or {}),
                }
            )
        )  # In sudo mode to allow writing on callback fields

        if flow == "token":
            tx_sudo._send_payment_request()  # Payments by token process transactions immediately
        else:
            tx_sudo._log_sent_message()

        # Monitor the transaction to make it available in the portal.
        PaymentPostProcessing.monitor_transaction(tx_sudo)

        return tx_sudo

    def _get_partner_sudo(self, user_sudo):
        """Get the partner of the user.
        Args:
            user_sudo: The user record in sudo mode.
        Returns:
            partner_sudo: The partner record in sudo mode.
        """
        partner_sudo = user_sudo.partner_id
        if not partner_sudo and user_sudo._is_public():
            partner_sudo = self.env.ref("base.public_user")
        return partner_sudo

    # Create a new transaction for the POS VNPay payment method
    def _create_new_transaction(self, pos_order_sudo, vnpay, order_amount):
        """Create a new transaction with POS VNPay payment method
        Args:
            pos_order_sudo: pos.order record in sudo mode
            vnpay: payment.provider vnpay record
            order_amount: The amount of the order
        Raises:
            AssertionError: If the currency is invalid
        Returns:
            tx_sudo: The created transaction record in sudo mode
        """

        # Get the access token of the POS order
        access_token = pos_order_sudo.access_token

        # Get the VNPay QR payment method
        vnpay_qr_method = (
            request.env["payment.method"]
            .sudo()
            .search([("code", "=", "vnpayqr")], limit=1)
        )

        # Get the user and partner of the user
        user_sudo = request.env.user
        partner_sudo = pos_order_sudo.partner_id or self._get_partner_sudo(user_sudo)

        # Create transaction data
        prefix_kwargs = {
            "pos_order_id": pos_order_sudo.id,
        }
        transaction_data = {
            "provider_id": vnpay.id,
            "payment_method_id": vnpay_qr_method.id,
            "partner_id": partner_sudo.id,
            "partner_phone": partner_sudo.phone,
            "token_id": None,
            "amount": int(order_amount),
            "flow": "direct",
            "tokenization_requested": False,
            "landing_route": "",
            "is_validation": False,
            "access_token": access_token,
            "reference_prefix": request.env["payment.transaction"]
            .sudo()
            ._compute_reference_prefix(
                provider_code="vnpay", separator="-", **prefix_kwargs
            ),
            "custom_create_values": {
                "pos_order_id": pos_order_sudo.id,
                "tokenize": False,
            },
        }

        # Check if the currency is valid
        currency = pos_order_sudo.currency_id
        if not currency.active:
            raise AssertionError(_("The currency is invalid."))
        # Ignore the currency provided by the customer
        transaction_data["currency_id"] = currency.id

        # Create a new transaction
        tx_sudo = self._create_transaction(**transaction_data)

        return tx_sudo

    @staticmethod
    def _validate_amount(pos_order_sudo, order_amount, receive_amount):
        """Validate the amount of the order.
        Args:
            pos_order_sudo: pos.order record in sudo mode
            order_amount: The amount of the order
            receive_amount: The amount received from the VNPay request
        Raises:
            AssertionError: If the amount is mismatched
        """
        if (
            order_amount
            and receive_amount
            and pos_order_sudo.currency_id.compare_amounts(
                float(order_amount), float(receive_amount)
            )
            == 0
        ):
            return
        else:
            raise AssertionError(_("Amount mismatched."))

    @staticmethod
    def _validate_checksum(data, secret_key):
        """Validate the checksum of the data received from the VNPay request.
        Args:
            data: data received from the VNPay request
            secret_key: The secret key of the VNPay payment provider
        Raises:
            Forbidden: If the checksum is mismatched
        """
        checksum = data.get("checksum")
        data_str = "|".join(
            [
                str(item) if item is not None else "null"
                for item in [
                    data.get("code"),
                    data.get("msgType"),
                    data.get("txnId"),
                    data.get("qrTrace"),
                    data.get("bankCode"),
                    data.get("mobile"),
                    data.get("accountNo"),
                    data.get("amount"),
                    data.get("payDate"),
                    data.get("merchantCode"),
                    secret_key,
                ]
            ]
        )

        res_checksum = hashlib.md5(data_str.encode()).hexdigest()

        if not hmac.compare_digest(res_checksum.capitalize(), checksum.capitalize()):
            raise Forbidden(_("Checksum mismatched."))

        return

    @http.route(
        _create_qr_url,
        type="json",
        methods=["POST"],
        auth="public",
        csrf=False,
    )
    def get_payment_url(self, orderId, amount):
        """Create a VNPay payment QR code and save a copy of the QR code data to the payment.qr model.
        Args:
            orderId: The POS order ID
            amount: The amount of the order
        Returns:
            img_base64: The base64 string of the QR code image
        """

        _logger.info("Creating VNPay payment QR.")

        try:
            # Get VNPay data
            vnpayqr = (
                http.request.env["payment.provider"]
                .sudo()
                .search([("code", "=", "vnpayqr")], limit=1)
            )

            # Create expire date for the QR code
            exp_date = datetime.now(pytz.timezone("Etc/GMT-7")) + timedelta(minutes=5)

            # field Datetime in Odoo do not accept timezone-aware datetime
            exp_date_naive = exp_date.replace(tzinfo=None)

            # Create the data for the QR code
            data = {
                "appId": vnpayqr.vnpayqr_app_id,
                "merchantName": vnpayqr.vnpayqr_merchant_name,
                "serviceCode": "03",
                "countryCode": "VN",
                "payloadFormat": "",
                "productId": "",
                "tipAndFee": "",
                "expDate": exp_date.strftime("%y%m%d%H%M"),
                "desc": "",
                "mobile": "",
                "consumerID": "",
                "purpose": "",
                "merchantCode": vnpayqr.vnpayqr_merchant_code,
                "terminalId": vnpayqr.vnpayqr_tmn_code,
                "payType": "03",
                "txnId": str(orderId),
                "billNumber": str(orderId),
                "amount": str(amount),
                "ccy": "704",
                "masterMerCode": "A000000775",
                "merchantType": vnpayqr.vnpayqr_merchant_type,
            }

            # Create a string from the data to create the checksum
            data_string = "|".join(
                [
                    data["appId"],
                    data["merchantName"],
                    data["serviceCode"],
                    data["countryCode"],
                    data["masterMerCode"],
                    data["merchantType"],
                    data["merchantCode"],
                    data["terminalId"],
                    data["payType"],
                    data["productId"],
                    data["txnId"],
                    data["amount"],
                    data["tipAndFee"],
                    data["ccy"],
                    data["expDate"],
                    vnpayqr.vnpayqr_secret_key,
                ]
            )

            # Create the checksum
            checksum = hashlib.md5(data_string.encode()).hexdigest()

            # Add the checksum to the data
            data["checksum"] = checksum

            qr_create_url = vnpayqr.vnpayqr_create_url

            data_json = json.dumps(data)

            # Send a POST request to the VNPay create QR URL
            response = pyreq.post(
                qr_create_url,
                data=data_json,
                headers={"Content-Type": "text/plain"},
            )

            # Parse the response body as JSON
            response_data = response.json()

            # Check the status code of the response
            if response.status_code == 200:

                # Check the error code in the response data
                error_code = response_data.get("code")
                if error_code == "00":

                    # Create a string from the response data to check the checksum
                    response_data_str = "|".join(
                        [
                            str(item) if item is not None else "null"
                            for item in [
                                response_data.get("code"),
                                response_data.get("message"),
                                response_data.get("data"),
                                response_data.get("url"),
                                vnpayqr.vnpayqr_secret_key,
                            ]
                        ]
                    )

                    res_checksum = (
                        hashlib.md5(response_data_str.encode()).hexdigest().capitalize()
                    )

                    # Check if the checksums match
                    if not hmac.compare_digest(
                        res_checksum, response_data.get("checksum").capitalize()
                    ):
                        return None

                    qrData = response_data.get("data")

                    # Generate QR code
                    qr = qrcode.QRCode(
                        version=1,
                        error_correction=qrcode.constants.ERROR_CORRECT_L,
                        box_size=10,
                        border=4,
                    )
                    qr.add_data(qrData)
                    qr.make(fit=True)

                    # Create an image from the QR Code instance
                    img = qr.make_image(fill="black", back_color="white")

                    # Save the image to a BytesIO object
                    buffer = BytesIO()
                    img.save(buffer, format="PNG")

                    # Get the content of the BytesIO object as bytes
                    img_bytes = buffer.getvalue()

                    # Convert the bytes to a base64 string
                    img_base64 = (
                        "data:image/png;base64," + base64.b64encode(img_bytes).decode()
                    )

                    # Save QR data to the database
                    http.request.env["payment.qr"].sudo().create(
                        {
                            "order_id": orderId,
                            "amount": amount,
                            "exp_date": exp_date_naive,
                            "qr_data": img_base64,
                        }
                    )

                    _logger.info("VNPay payment QR created successfully.")

                    return img_base64

                else:
                    message = response_data.get("message")
                    _logger.error(
                        f"Receive data with error code: {error_code} and message: {message}"
                    )
                    return None
            else:
                _logger.error(
                    f"Request to create payment QR failed with status code {response.status_code}."
                )
                return None
        except Exception as e:
            _logger.error(f"Error creating VNPay payment QR: {e}")
            return None

    @http.route(
        _pos_ipn_url,
        type="http",
        methods=["POST"],
        auth="public",
        csrf=False,
    )
    def handle_ipn(self, **kwargs):
        """Handle the IPN request from VNPay.
        Raises:
            ValidationError: If the transaction is not found
        Returns:
            Response to VNPay the result of the IPN request
        """

        # Get the data from the request
        data = request.get_json_data()

        _logger.info("Received IPN data. %s", data)

        try:
            # Get the VNPay data
            vnpayqr = (
                request.env["payment.provider"]
                .sudo()
                .search([("code", "=", "vnpayqr")], limit=1)
            )

            _logger.info("Processing IPN data.")

            # Validate the checksum
            self._validate_checksum(data, vnpayqr.vnpayqr_secret_key)

            # Get the POS order with the txnId
            pos_order_sudo = (
                request.env["pos.order"]
                .sudo()
                .search([("id", "=", data.get("txnId"))], limit=1)
            )

            # Check if the order exists
            if not pos_order_sudo:
                raise ValidationError(_("No transaction found matching reference."))

            # Check if the order has been paid
            if pos_order_sudo.state in ("paid", "done", "invoiced"):
                _logger.info("Order has been paid. Aborting.")
                res = {
                    "code": "03",
                    "message": "Đơn hàng đã được thanh toán.",
                    "data": {
                        "txnId": data.get("txnId"),
                    },
                }
                return request.make_json_response(res)

            # Create a new transaction
            _logger.info("Creating new transaction.")
            order_amount = pos_order_sudo._get_checked_next_online_payment_amount()
            tx_sudo = self._create_new_transaction(
                pos_order_sudo, vnpayqr, order_amount
            )

            # Validate the amount
            receive_amount = data.get("amount")
            self._validate_amount(pos_order_sudo, order_amount, receive_amount)

            # Check if QR code has expired
            order_qr = (
                http.request.env["payment.qr"]
                .sudo()
                .search([("order_id", "=", data.get("txnId"))], limit=1)
            )
            # get current time in UTC +7
            current_time = datetime.now(pytz.timezone("Etc/GMT-7"))

            # remove the UTC info
            current_time_naive = current_time.replace(tzinfo=None)
            _logger.info("current_time: %s", current_time_naive)
            _logger.info("order_qr.exp_date: %s", order_qr.exp_date)
            _logger.info("is expired: %s", current_time_naive > order_qr.exp_date)
            if current_time_naive > order_qr.exp_date:
                _logger.info("QR code has expired. Aborting.")
                tx_sudo._set_error(
                    "VNPay-QR: " + _("Received payment for expired QR. Aborting.")
                )
                res = {
                    "code": "09",
                    "message": "QR hết hạn thanh toán.",
                }
                return request.make_json_response(res)

            # Update the transaction "provider_reference" with the qrTrace data
            tx_sudo.provider_reference = data.get("qrTrace")

            # Check the response code and process the payment
            res_code = data.get("code")

            if res_code == "00":
                _logger.info("Payment processed successfully. Saving.")

                # Set the transaction as done and process the payment
                tx_sudo._set_done()
                tx_sudo._process_pos_online_payment()
                _logger.info("Payment saved successfully.")
                res = {
                    "code": "00",
                    "message": "Đặt hàng thành công.",
                    "data": {
                        "txnId": data.get("txnId"),
                    },
                }
                return request.make_json_response(res)
            else:
                _logger.warning(
                    "Received data with invalid response code: %s. Aborting.",
                    res_code,
                )
                tx_sudo._set_error(
                    "VNPay-QR: "
                    + _("Received data with invalid response code: %s", res_code)
                )
                res = {
                    "code": "04",
                    "message": f"Nhận dữ liệu với mã lỗi là: {res_code}",
                }
                return request.make_json_response(res)

        except Forbidden:
            _logger.warning(
                "Forbidden error during notification handling. Aborting.",
                exc_info=True,
            )
            res = {
                "code": "06",
                "message": "Sai thông tin xác thực.",
            }
            return request.make_json_response(res)

        except AssertionError:
            _logger.warning(
                "Assertion error during notification handling. Aborting.",
                exc_info=True,
            )
            tx_sudo._set_error("VNPay-QR: " + _("Received data with invalid amount."))
            res = {
                "code": "07",
                "message": "Số tiền không chính xác.",
                "data": {
                    "amount": f"{int(order_amount)}",
                },
            }
            return request.make_json_response(res)

        except ValidationError:
            _logger.warning(
                "Validation error during notification handling. Aborting.",
                exc_info=True,
            )
            res = {
                "code": "04",
                "message": "Không tìm thấy txnId trong hệ thống.",
            }
            return request.make_json_response(res)

        except Exception as e:
            _logger.error(f"Error processing IPN data: {e}")
            res = {"code": "04", "message": f"Lỗi hệ thống khi xử lý thông tin: {e}"}
            return request.make_json_response(res)
