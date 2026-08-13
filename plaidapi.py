#!/usr/bin/env python3

import re
import datetime

import plaid
from plaid.api import plaid_api
from typing import Optional, List
from plaid.model.item_public_token_exchange_request import (
    ItemPublicTokenExchangeRequest,
)
from plaid.model.link_token_create_request import LinkTokenCreateRequest
from plaid.model.link_token_transactions import LinkTokenTransactions
from plaid.model.item_get_request import ItemGetRequest
from plaid.model.country_code import CountryCode
from plaid.model.products import Products
from plaid.model.accounts_balance_get_request import AccountsBalanceGetRequest
from plaid.model.transactions_get_request import TransactionsGetRequest
from plaid.model.link_token_create_request_user import LinkTokenCreateRequestUser
from plaid.model.transactions_get_request_options import TransactionsGetRequestOptions
from plaid.model.transactions_sync_request import TransactionsSyncRequest
from plaid.model.sandbox_item_reset_login_request import SandboxItemResetLoginRequest
from plaid.model.investments_transactions_get_request import (
    InvestmentsTransactionsGetRequest,
)
from plaid.model.investments_transactions_get_request_options import (
    InvestmentsTransactionsGetRequestOptions,
)
from plaid.model.investments_holdings_get_request import InvestmentsHoldingsGetRequest


class AccountBalance:
    def __init__(self, data):
        self.raw_data = data
        self.account_id = data["account_id"]
        self.account_name = data["name"]
        self.account_type = data["type"]
        self.account_subtype = data["subtype"]
        self.account_number = data["mask"]
        self.balance_current = data["balances"]["current"]
        self.balance_available = data["balances"]["available"]
        self.balance_limit = data["balances"]["limit"]
        self.currency_code = data["balances"]["iso_currency_code"]


class AccountInfo:
    def __init__(self, data):
        self.raw_data = data
        self.item_id = data["item"]["item_id"]
        self.institution_id = data["item"]["institution_id"]
        # Products the user consented to. Checked before an investments call,
        # which otherwise fails ADDITIONAL_CONSENT_REQUIRED.
        self.consented_products = list(data["item"].get("consented_products") or [])
        self.ts_consent_expiration = data["item"]["consent_expiration_time"]
        self.ts_last_failed_update = data["status"]["transactions"][
            "last_failed_update"
        ]
        self.ts_last_successful_update = data["status"]["transactions"][
            "last_successful_update"
        ]


class Transaction:
    def __init__(self, data):
        self.raw_data = data
        self.account_id = data["account_id"]
        self.date = data["date"]
        self.transaction_id = data["transaction_id"]
        self.pending = data["pending"]
        self.merchant_name = data["merchant_name"]
        self.amount = data["amount"]
        self.currency_code = data["iso_currency_code"]
        self.personal_finance_category = data["personal_finance_category"]

    def __str__(self):
        return "%s %s %s - %4.2f %s" % (
            self.date,
            self.transaction_id,
            self.merchant_name,
            self.amount,
            self.currency_code,
        )


class InvestmentTransaction:
    """A buy, sell, dividend, fee or transfer inside a brokerage/IRA account.

    Distinct from Transaction: it carries a security_id, a share quantity and a
    price, and its identifier is investment_transaction_id. Kept as a separate
    type (and a separate table) so the plain-cash transaction stream downstream
    consumers read is not polluted with share lots.
    """

    def __init__(self, data):
        self.raw_data = data
        self.account_id = data["account_id"]
        self.investment_transaction_id = data["investment_transaction_id"]
        self.security_id = data.get("security_id")
        self.date = data["date"]
        self.name = data.get("name")
        self.quantity = data.get("quantity")
        self.amount = data.get("amount")
        self.price = data.get("price")
        self.fees = data.get("fees")
        self.type = data.get("type")
        self.subtype = data.get("subtype")
        self.currency_code = data.get("iso_currency_code")

    def __str__(self):
        return "%s %s %s - %s @ %s" % (
            self.date,
            self.investment_transaction_id,
            self.name,
            self.quantity,
            self.price,
        )


class Security:
    """A tradable instrument referenced by an InvestmentTransaction or holding."""

    def __init__(self, data):
        self.raw_data = data
        self.security_id = data["security_id"]
        self.name = data.get("name")
        self.ticker_symbol = data.get("ticker_symbol")
        self.type = data.get("type")
        self.currency_code = data.get("iso_currency_code")


class Holding:
    """A current position: how many shares of a security an account holds."""

    def __init__(self, data):
        self.raw_data = data
        self.account_id = data["account_id"]
        self.security_id = data["security_id"]
        self.quantity = data.get("quantity")
        self.cost_basis = data.get("cost_basis")
        self.institution_price = data.get("institution_price")
        self.institution_value = data.get("institution_value")
        self.currency_code = data.get("iso_currency_code")


def parse_optional_iso8601_timestamp(ts: Optional[str]) -> datetime.datetime:
    if ts is None:
        return None
    # sometimes the milliseconds coming back from plaid have less than 3 digits
    # which fromisoformat hates - it also hates "Z", so strip those off from this
    # string (the milliseconds hardly matter for this purpose, and I'd rather avoid
    # having to pull dateutil JUST for this parsing)
    return datetime.datetime.fromisoformat(re.sub(r"[.][0-9]+Z", "+00:00", ts))


def raise_plaid(ex: plaid.ApiException):
    if ex.reason == "NO_ACCOUNTS":
        raise PlaidNoApplicableAccounts(ex)
    elif ex.reason == "ITEM_LOGIN_REQUIRED":
        raise PlaidAccountUpdateNeeded(ex)
    else:
        raise PlaidUnknownError(ex)


def wrap_plaid_error(f):
    def wrap(*args, **kwargs):
        try:
            return f(*args, **kwargs)
        except plaid.ApiException as ex:
            raise_plaid(ex)

    return wrap


class PlaidError(Exception):
    def __init__(self, plaid_error):
        super().__init__()
        self.plaid_error = plaid_error
        self.message = plaid_error.reason

    def __str__(self):
        return "%s: %s" % (self.plaid_error.status, self.plaid_error.body)


class PlaidUnknownError(PlaidError):
    pass


class PlaidNoApplicableAccounts(PlaidError):
    pass


class PlaidAccountUpdateNeeded(PlaidError):
    pass


class PlaidAPI:
    def __init__(
        self, client_id: str, secret: str, environment: str, suppress_warnings=True
    ):
        # Map environment string to proper enum value
        env_mapping = {
            "sandbox": plaid.Environment.Sandbox,
            "production": plaid.Environment.Production,
        }

        host = env_mapping.get(environment.lower(), plaid.Environment.Production)

        configuration = plaid.Configuration(
            host=host, api_key={"clientId": client_id, "secret": secret}
        )
        api_client = plaid.ApiClient(configuration)
        self.client = plaid_api.PlaidApi(api_client)

    @wrap_plaid_error
    def get_link_token(self, access_token=None, user_id="user",
                       days_requested=None,
                       additional_consented_products=None) -> str:
        """
        Calls the /link/token/create workflow, which returns an access token
        which can be used to initate the account linking process or, if an access_token
        is provided, to update an existing linked account.

        This token is used by the web-browser/JavaScript API to exchange for a public
        token to finalize the linking process.

        https://plaid.com/docs/api/tokens/#token-exchange-flow
        """

        user = LinkTokenCreateRequestUser(client_user_id=user_id)

        req_data = {
            "client_name": "plaid-sync",
            "country_codes": [CountryCode("US")],
            "language": "en",
            "user": user,
        }

        # if updating an existing account, the products field is not allowed
        if access_token:
            req_data["access_token"] = access_token
        else:
            req_data["products"] = [Products("transactions")]

        # How far back Plaid fetches history for this item. Valid when linking and
        # when updating; omitting it lets Plaid apply its 90-day default.
        if days_requested:
            req_data["transactions"] = LinkTokenTransactions(
                days_requested=days_requested
            )

        # Products the user consents to beyond the one being linked. Valid in
        # update mode too, which is how an existing item gains investments access
        # without being re-linked.
        if additional_consented_products:
            req_data["additional_consented_products"] = [
                Products(p) for p in additional_consented_products
            ]

        req = LinkTokenCreateRequest(**req_data)
        response = self.client.link_token_create(req)
        return response["link_token"]

    @wrap_plaid_error
    def exchange_public_token(self, public_token: str) -> str:
        """
        Exchange a temporary public token for a permanent private
        access token.
        """
        req = ItemPublicTokenExchangeRequest(public_token=public_token)
        response = self.client.item_public_token_exchange(req)
        return response["access_token"]

    @wrap_plaid_error
    def sandbox_reset_login(self, access_token: str) -> str:
        """
        Only applicable to sandbox environment. Resets the login
        details for a specific account so you can test the update
        account flow.

        Otherwise, attempting to update will just display "Account
        already connected." in the Plaid browser UI.
        """

        req = SandboxItemResetLoginRequest(access_token=access_token)
        return self.client.sandbox_item_reset_login(req)

    @wrap_plaid_error
    def get_item_info(self, access_token: str) -> AccountInfo:
        """
        Returns account information associated with this particular access token.
        """
        req = ItemGetRequest(access_token=access_token)
        resp = self.client.item_get(req)
        return AccountInfo(resp.to_dict())

    @wrap_plaid_error
    def get_account_balance(self, access_token: str) -> List[AccountBalance]:
        """
        Returns the balances of all accounts associated with this particular access_token.
        """
        req = AccountsBalanceGetRequest(access_token=access_token)
        resp = self.client.accounts_balance_get(req)
        return list(map(AccountBalance, resp.to_dict()["accounts"]))

    @wrap_plaid_error
    def get_transactions(
        self,
        access_token: str,
        start_date: datetime.date,
        end_date: datetime.date,
        account_ids: Optional[List[str]] = None,
        status_callback=None,
    ):
        # Import the options class

        ret = []
        total_transactions = None
        offset = 0
        count = 500  # Maximum allowed by Plaid API

        print(start_date)

        while True:
            # Create options object with pagination parameters
            options = TransactionsGetRequestOptions(count=count, offset=offset)

            if account_ids:
                options.account_ids = account_ids

            req = TransactionsGetRequest(
                access_token=access_token,
                start_date=start_date,
                end_date=end_date,
                options=options,
            )

            response = self.client.transactions_get(req)
            response_dict = response.to_dict()

            total_transactions = response_dict["total_transactions"]
            print(total_transactions)
            transactions_batch = [Transaction(t) for t in response_dict["transactions"]]
            ret += transactions_batch

            if status_callback:
                status_callback(len(ret), total_transactions)

            # If we got fewer transactions than requested, we've reached the end
            if len(transactions_batch) < count or len(ret) >= total_transactions:
                break

            # Move to the next batch
            offset += count

        return ret

    @wrap_plaid_error
    def get_investment_transactions(
        self,
        access_token: str,
        start_date: datetime.date,
        end_date: datetime.date,
        status_callback=None,
    ):
        """
        Fetch investment activity (buys, sells, dividends, fees, transfers) for
        every investment account on this item.

        There is no cursor-based equivalent of /transactions/sync for
        investments, so this is a date range and callers re-fetch the whole
        window each run. That is safe: storage upserts on
        investment_transaction_id, which is stable for the life of the item.

        Returns {'transactions': [InvestmentTransaction], 'securities': [Security]}.
        Securities come back as a separate list keyed by security_id -- the
        transactions only reference them by id, so both must be stored to make
        sense of a lot later.
        """
        transactions = []
        securities = {}
        offset = 0
        count = 500  # Plaid's maximum page size

        while True:
            req = InvestmentsTransactionsGetRequest(
                access_token=access_token,
                start_date=start_date,
                end_date=end_date,
                options=InvestmentsTransactionsGetRequestOptions(
                    count=count, offset=offset
                ),
            )
            response = self.client.investments_transactions_get(req).to_dict()

            batch = [
                InvestmentTransaction(t)
                for t in response.get("investment_transactions", [])
            ]
            transactions += batch
            for security in response.get("securities", []):
                securities[security["security_id"]] = Security(security)

            total = response.get("total_investment_transactions", len(transactions))
            if status_callback:
                status_callback(len(transactions), total)

            if len(batch) < count or len(transactions) >= total:
                break
            offset += count

        return {
            "transactions": transactions,
            "securities": list(securities.values()),
        }

    @wrap_plaid_error
    def get_investment_holdings(self, access_token: str):
        """
        Current positions per investment account, with the securities they name.

        Holdings are a point-in-time snapshot rather than a history, which is
        what makes them useful for a retirement account: even where Plaid can
        offer no transaction history, the holding still anchors a Beancount
        balance assertion.
        """
        response = self.client.investments_holdings_get(
            InvestmentsHoldingsGetRequest(access_token=access_token)
        ).to_dict()
        return {
            "holdings": [Holding(h) for h in response.get("holdings", [])],
            "securities": [Security(s) for s in response.get("securities", [])],
        }

    @wrap_plaid_error
    def sync_transactions(
        self,
        access_token: str,
        cursor: Optional[str] = None,
        status_callback=None,
    ):
        """
        Sync transactions using Plaid's /transactions/sync endpoint.
        This is more efficient than get_transactions as it only fetches updates.

        Args:
            access_token: The access token for the account
            cursor: Cursor from previous sync (None for initial sync)
            status_callback: Optional callback function to report progress

        Returns:
            dict with keys: 'added', 'modified', 'removed', 'cursor', 'has_next'
        """

        all_added = []
        all_modified = []
        all_removed = []
        current_cursor = cursor
        has_next = True

        while has_next:
            if current_cursor is not None:
                req = TransactionsSyncRequest(
                    access_token=access_token, cursor=current_cursor
                )
            else:
                req = TransactionsSyncRequest(access_token=access_token)

            response = self.client.transactions_sync(req)
            response_dict = response.to_dict()

            # Add transactions from this batch
            batch_added = [Transaction(t) for t in response_dict.get("added", [])]
            batch_modified = [Transaction(t) for t in response_dict.get("modified", [])]
            batch_removed = [removed['transaction_id'] for removed in response_dict.get("removed", [])]

            all_added.extend(batch_added)
            all_modified.extend(batch_modified)
            all_removed.extend(batch_removed)

            # Update cursor and check if more pages exist
            current_cursor = response_dict["next_cursor"]
            has_next = response_dict["has_more"]

            if status_callback:
                status_callback(
                    len(all_added), len(all_modified), len(all_removed), has_next
                )

        return {
            "added": all_added,
            "modified": all_modified,
            "removed": all_removed,
            "cursor": current_cursor,
            "has_next": has_next,
        }
