# Part of Odoo. See LICENSE file for full copyright and licensing details.

{
    "name": "POS Payment: VNPay",
    "version": "2.3",
    "category": "Point of Sale",
    "sequence": 0,
    "summary": "This module integrates the VNPay payment method into the POS system.",
    "description": " ",  # Non-empty string to avoid loading the README file.
    "author": "Nguyen Phuc Huy",
    "depends": ["point_of_sale", "account_payment"],
    "data": [
        "security/ir.model.access.csv",
        "views/pos_vnpay_settings.xml",
    ],
    "assets": {
        "point_of_sale.assets_prod": [
            "pos_vnpay/static/src/js/pos_online_payment/*",
            "pos_vnpay/static/src/xml/*",
        ],
    },
    "post_init_hook": "post_init_hook",
    "uninstall_hook": "uninstall_hook",
    "installable": True,
    "auto_install": False,
    "application": True,
    "license": "LGPL-3",
}
