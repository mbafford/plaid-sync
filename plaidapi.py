#!/python3

import re
import datetime

import plaid
from typing import Optional, List


class AccountBalance:
    def __init__(self, data):
        self.raw_data = data
        self.account_id        = data['account_id']
        self.account_name      = data['name']
        self.account_type      = data['type']
        self.account_subtype   = data['subtype']
        self.account_number    = data['mask']
        self.balance_current   = data['balances']['current']
        self.balance_available = data['balances']['available']
        self.balance_limit     = data['balances']['limit']
        self.currency_code     = data['balances']['iso_currency_code']


class AccountInfo:
    def __init__(self, data):
        self.raw_data = data
        self.item_id                   = data['item']['item_id']
        self.institution_id            = data['item']['institution_id']
        self.ts_consent_expiration     = parse_optional_iso8601_timestamp(data['item']['consent_expiration_time'])
        self.ts_last_failed_update     = parse_optional_iso8601_timestamp(data['status']['transactions']['last_failed_update'])
        self.ts_last_successful_update = parse_optional_iso8601_timestamp(data['status']['transactions']['last_successful_update'])


class Transaction:
    def __init__(self, data):
        self.raw_data = data
        self.account_id     = data['account_id']
        self.date           = data['date']
        self.transaction_id = data['transaction_id']
        self.pending        = data['pending']
        self.merchant_name  = data['merchant_name']
        self.amount         = data['amount']
        self.currency_code  = data['iso_currency_code']

    def __str__(self):
        return "%s %s %s - %4.2f %s" % ( self.date, self.transaction_id, self.merchant_name, self.amount, self.currency_code )


def parse_optional_iso8601_timestamp(ts: Optional[str]) -> datetime.datetime:
    if ts is None:
        return None
    # sometimes the milliseconds coming back from plaid have less than 3 digits
    # which fromisoformat hates - it also hates "Z", so strip those off from this
    # string (the milliseconds hardly matter for this purpose, and I'd rather avoid
    # having to pull dateutil JUST for this parsing)
    return datetime.datetime.fromisoformat(re.sub(r"[.][0-9]+Z", "+00:00", ts))


def raise_plaid(ex: plaid.errors.ItemError):
    if ex.code == 'NO_ACCOUNTS':
        raise PlaidNoApplicableAccounts(ex)
    elif ex.code == 'ITEM_LOGIN_REQUIRED':
        raise PlaidAccountUpdateNeeded(ex)
    else:
        raise PlaidUnknownError(ex)


def wrap_plaid_error(f):
    def wrap(*args, **kwargs):
        try:
            return f(*args, **kwargs)
        except plaid.errors.PlaidError as ex:
            raise_plaid(ex)
    return wrap


class PlaidError(Exception):
    def __init__(self, plaid_error):
        super().__init__()
        self.plaid_error = plaid_error
        self.message = plaid_error.message

    def __str__(self):
        return "%s: %s" % (self.plaid_error.code, self.message)


class PlaidUnknownError(PlaidError):
    pass


class PlaidNoApplicableAccounts(PlaidError):
    pass


class PlaidAccountUpdateNeeded(PlaidError):
    pass


class PlaidAPI():
    def __init__(self, client_id: str, secret: str, environment: str, suppress_warnings=True):
        self.client = plaid.Client(
            client_id,
            secret,
            environment,
            suppress_warnings
        )

    @wrap_plaid_error
    def get_link_token(self, access_token=None) -> str:
        """
        Calls the /link/token/create workflow, which returns an access token
        which can be used to initate the account linking process or, if an access_token
        is provided, to update an existing linked account.

        This token is used by the web-browser/JavaScript API to exchange for a public
        token to finalize the linking process.

        https://plaid.com/docs/api/tokens/#token-exchange-flow
        """

        data = {
            'user': {
                'client_user_id': 'abc123',
            },
            'client_name': 'plaid-sync',
            'country_codes': ['US'],
            'language': 'en',
        }

        # if updating an existing account, the products field is not allowed
        if access_token:
            data['access_token'] = access_token
        else:
            data['products'] = ['transactions']            

        return self.client.post('/link/token/create', data)['link_token']

    @wrap_plaid_error
    def exchange_public_token(self, public_token: str) -> str:
        """
        Exchange a temporary public token for a permanent private
        access token.
        """
        return self.client.Item.public_token.exchange(public_token)

    @wrap_plaid_error
    def sandbox_reset_login(self, access_token: str) -> str:
        """
        Only applicable to sandbox environment. Resets the login
        details for a specific account so you can test the update
        account flow. 

        Otherwise, attempting to update will just display "Account
        already connected." in the Plaid browser UI.
        """
        return self.client.post('/sandbox/item/reset_login', {
            'access_token': access_token,
        })

    @wrap_plaid_error
    def get_item_info(self, access_token: str)->AccountInfo:
        """
        Returns account information associated with this particular access token.
        """
        resp = self.client.Item.get(access_token)
        return AccountInfo(resp)

    @wrap_plaid_error
    def get_account_balance(self, access_token:str)->List[AccountBalance]:
        """
        Returns the balances of all accounts associated with this particular access_token.
        """
        resp = self.client.Accounts.balance.get(access_token=access_token)
        return list( map( AccountBalance, resp['accounts'] ) )

    @wrap_plaid_error
    def get_transactions(self, access_token:str, start_date:datetime.date, end_date:datetime.date, account_ids:Optional[List[str]]=None, status_callback=None):
        ret = []
        total_transactions = None
        while True:
            response = self.client.Transactions.get(
                            access_token,
                            start_date.strftime("%Y-%m-%d"),
                            end_date.strftime("%Y-%m-%d"),
                            account_ids=account_ids,
                            offset=len(ret),
                            count=500)

            total_transactions = response['total_transactions']

            ret += [
                Transaction(t)
                for t in response['transactions']
            ]

            if status_callback: status_callback(len(ret), total_transactions)
            if len(ret) >= total_transactions: break

        return ret
