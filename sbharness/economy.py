"""Purely local pretend wallet, exchange table, and membership plans."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP


# Claude.ai availability snapshot captured from Anthropic's official supported-region
# page; refresh only when packaging a release and only after its effective date.
CLAUDE_REGION_SNAPSHOT = "2026-03-16"

COUNTRIES = [
    ("US", "美国", "USD"), ("CN", "中国大陆", "CNY"), ("TW", "台湾地区", "TWD"),
    ("HK", "香港", "HKD"), ("MO", "澳门", "MOP"), ("JP", "日本", "JPY"),
    ("KR", "韩国", "KRW"), ("SG", "新加坡", "SGD"), ("GB", "英国", "GBP"),
    ("FR", "法国", "EUR"), ("DE", "德国", "EUR"), ("IT", "意大利", "EUR"),
    ("ES", "西班牙", "EUR"), ("NL", "荷兰", "EUR"), ("CA", "加拿大", "CAD"),
    ("AU", "澳大利亚", "AUD"), ("NZ", "新西兰", "NZD"), ("IN", "印度", "INR"),
    ("BR", "巴西", "BRL"), ("MX", "墨西哥", "MXN"), ("TH", "泰国", "THB"),
    ("ID", "印度尼西亚", "IDR"), ("VN", "越南", "VND"), ("MY", "马来西亚", "MYR"),
    ("PH", "菲律宾", "PHP"), ("CH", "瑞士", "CHF"), ("SE", "瑞典", "SEK"),
    ("NO", "挪威", "NOK"), ("DK", "丹麦", "DKK"), ("PL", "波兰", "PLN"),
    ("TR", "土耳其", "TRY"), ("AE", "阿联酋", "AED"), ("SA", "沙特阿拉伯", "SAR"),
    ("ZA", "南非", "ZAR"), ("AR", "阿根廷", "ARS"), ("PK", "巴基斯坦", "PKR"),
    ("BD", "孟加拉国", "BDT"), ("EG", "埃及", "EGP"), ("NG", "尼日利亚", "NGN"),
    ("RU", "俄罗斯", "RUB"), ("IR", "伊朗", "IRR"), ("KP", "朝鲜", "KPW"),
    ("CU", "古巴", "CUP"), ("SY", "叙利亚", "SYP"), ("UA", "乌克兰", "UAH"),
    ("IL", "以色列", "ILS"), ("IE", "爱尔兰", "EUR"), ("PT", "葡萄牙", "EUR"),
    ("BE", "比利时", "EUR"), ("AT", "奥地利", "EUR"), ("FI", "芬兰", "EUR"),
    ("ZZ", "其他未列地区", "USD"),
]

# Offline play-money rates: units of currency per fictional USD. These are deliberately
# fixed and are not a quote from a live financial service.
USD_RATES = {
    "USD": Decimal("1"), "CNY": Decimal("7.20"), "TWD": Decimal("32.0"),
    "HKD": Decimal("7.80"), "MOP": Decimal("8.00"), "JPY": Decimal("150"),
    "KRW": Decimal("1350"), "SGD": Decimal("1.35"), "GBP": Decimal("0.79"),
    "EUR": Decimal("0.92"), "CAD": Decimal("1.37"), "AUD": Decimal("1.52"),
    "NZD": Decimal("1.65"), "INR": Decimal("83"), "BRL": Decimal("5.1"),
    "MXN": Decimal("17"), "THB": Decimal("36"), "IDR": Decimal("16000"),
    "VND": Decimal("25000"), "MYR": Decimal("4.7"), "PHP": Decimal("57"),
    "CHF": Decimal("0.88"), "SEK": Decimal("10.5"), "NOK": Decimal("10.6"),
    "DKK": Decimal("6.9"), "PLN": Decimal("4.0"), "TRY": Decimal("33"),
    "AED": Decimal("3.67"), "SAR": Decimal("3.75"), "ZAR": Decimal("18.5"),
    "ARS": Decimal("950"), "PKR": Decimal("278"), "BDT": Decimal("117"),
    "EGP": Decimal("48"), "NGN": Decimal("1500"), "RUB": Decimal("90"),
    "IRR": Decimal("42000"), "KPW": Decimal("900"), "CUP": Decimal("24"),
    "SYP": Decimal("13000"), "UAH": Decimal("41"), "ILS": Decimal("3.7"),
}

SUPPORTED_CLAUDE_REGIONS = frozenset({
    "AL", "DZ", "AD", "AO", "AG", "AR", "AM", "AU", "AT", "AZ", "BS", "BH",
    "BD", "BB", "BE", "BZ", "BJ", "BT", "BO", "BA", "BW", "BR", "BN", "BG",
    "BF", "BI", "KH", "CM", "CA", "CV", "TD", "CL", "CO", "KM", "CG", "CR",
    "CI", "HR", "CZ", "DK", "DJ", "DM", "DO", "TL", "EC", "EG", "SV", "GQ",
    "EE", "SZ", "FJ", "FI", "FR", "GA", "GM", "GE", "DE", "GH", "GR", "GD",
    "GT", "GN", "GW", "GY", "HT", "HN", "HU", "IS", "IN", "ID", "IQ", "IE",
    "IL", "IT", "JM", "JP", "JO", "KZ", "KE", "KI", "KW", "KG", "LA", "LV",
    "LB", "LS", "LR", "LI", "LT", "LU", "MG", "MW", "MY", "MV", "MT", "MP",
    "MH", "MR", "MU", "MX", "FM", "MD", "MC", "MN", "ME", "MA", "MZ", "NA",
    "NR", "NP", "NL", "NZ", "NE", "NG", "MK", "NO", "OM", "PK", "PW", "PS",
    "PA", "PG", "PY", "PE", "PH", "PL", "PT", "QA", "CY", "RO", "RW", "KN",
    "LC", "VC", "WS", "SM", "ST", "SA", "SN", "RS", "SC", "SL", "SG", "SK",
    "SI", "SB", "ZA", "KR", "ES", "LK", "SR", "SE", "CH", "TW", "TJ", "TZ",
    "TH", "TG", "TO", "TT", "TN", "TR", "TM", "TV", "UG", "UA", "AE", "GB",
    "US", "UY", "UZ", "VU", "VA", "VN", "ZM", "ZW",
})

PLANS = {
    "Plus": Decimal("5"),
    "Pro": Decimal("20"),
    "Pro5x": Decimal("100"),
    "Pro20x": Decimal("200"),
}


def currency_for_country(country: str) -> str:
    return next((currency for code, _name, currency in COUNTRIES if code == country), "USD")


def country_name(code: str) -> str:
    return next((name for ident, name, _currency in COUNTRIES if ident == code), code)


def currency_digits(currency: str) -> int:
    return 0 if currency in {"JPY", "KRW", "IDR", "VND", "KPW", "IRR"} else 2


def round_currency(value: Decimal, currency: str) -> Decimal:
    digits = currency_digits(currency)
    quantum = Decimal("1") if digits == 0 else Decimal("0.01")
    return value.quantize(quantum, rounding=ROUND_HALF_UP)


def format_money(value: Decimal | str, currency: str) -> str:
    amount = round_currency(Decimal(str(value)), currency)
    return f"{amount:,.{currency_digits(currency)}f} {currency}"


class WalletService:
    def __init__(self, state: dict, save=lambda: None):
        self.state = state
        self.save = save
        self.state.setdefault("wallet", {"balance": "0.00", "currency": "USD", "ledger": []})
        self.state["wallet"].setdefault("ledger", [])
        self.state.setdefault("membership", {"tier": "Free", "expires_at": None})

    @property
    def balance(self) -> Decimal:
        return Decimal(str(self.state["wallet"].get("balance", "0")))

    @property
    def currency(self) -> str:
        return self.state["wallet"].get("currency", "USD")

    def display_balance(self) -> str:
        return format_money(self.balance, self.currency)

    def adjust(self, amount: Decimal | str | int | float, reason: str = "AI 钱包调整") -> dict:
        amount = Decimal(str(amount))
        if not amount.is_finite() or abs(amount) > Decimal("1000000000000"):
            raise ValueError("金额超出允许范围")
        amount = round_currency(amount, self.currency)
        before = self.balance
        after = round_currency(before + amount, self.currency)
        self.state["wallet"]["balance"] = str(after)
        entry = {
            "kind": "adjustment", "amount": str(amount), "currency": self.currency,
            "before": str(before), "after": str(after), "reason": (reason or "钱包调整")[:160],
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        self.state["wallet"]["ledger"].append(entry)
        self.save()
        return entry

    def change_country(self, country: str) -> dict | None:
        new_currency = currency_for_country(country)
        old_currency = self.currency
        self.state.setdefault("profile", {})["country"] = country
        if new_currency == old_currency:
            self.save()
            return None
        old_rate = USD_RATES.get(old_currency, Decimal("1"))
        new_rate = USD_RATES.get(new_currency, Decimal("1"))
        before = self.balance
        after = round_currency(before / old_rate * new_rate, new_currency)
        entry = {
            "kind": "currency_conversion", "before": str(before), "from": old_currency,
            "after": str(after), "to": new_currency, "rate_from_usd": str(old_rate),
            "rate_to_usd": str(new_rate), "created_at": datetime.now(timezone.utc).isoformat(),
        }
        wallet = self.state["wallet"]
        wallet["balance"], wallet["currency"] = str(after), new_currency
        wallet["ledger"].append(entry)
        self.save()
        return entry

    def purchase(self, tier: str) -> dict:
        if tier not in PLANS:
            raise ValueError("未知会员等级")
        current = self.membership_status()
        if current["tier"] != "Free":
            raise ValueError("当前会员尚未到期")
        price = round_currency(PLANS[tier] * USD_RATES.get(self.currency, Decimal("1")), self.currency)
        if self.balance < price:
            raise ValueError(f"余额不足，开通需要 {format_money(price, self.currency)}")
        self.adjust(-price, f"开通 {tier} 会员（30 天模拟订阅）")
        now = datetime.now(timezone.utc)
        expires = now + timedelta(days=30)
        self.state["membership"] = {"tier": tier, "activated_at": now.isoformat(), "expires_at": expires.isoformat()}
        entry = {"kind": "membership", "tier": tier, "price": str(price), "currency": self.currency, "expires_at": expires.isoformat()}
        self.state["wallet"]["ledger"].append(entry)
        self.save()
        return entry

    def membership_status(self, now: datetime | None = None) -> dict:
        item = self.state.get("membership", {"tier": "Free", "expires_at": None})
        expires = item.get("expires_at")
        if expires:
            try:
                expiry = datetime.fromisoformat(expires)
                current = now or datetime.now(timezone.utc)
                if expiry <= current:
                    self.state["membership"] = {"tier": "Free", "expires_at": None}
                    self.save()
                    return self.state["membership"]
            except ValueError:
                self.state["membership"] = {"tier": "Free", "expires_at": None}
                self.save()
                return self.state["membership"]
        return item
