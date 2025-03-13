from odoo import models, fields


class PaymentQR(models.Model):
    _name = "payment.qr"
    _description = "Payment QR Code"

    order_id = fields.Char(string="Order ID", required=True)
    amount = fields.Char(string="Amount", required=True)
    exp_date = fields.Datetime(string="Expiration Date", required=True)
    qr_data = fields.Text(string="QR Data", required=True)
