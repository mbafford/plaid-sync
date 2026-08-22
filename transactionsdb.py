#!python3

import sqlite3
import json
import datetime

from typing import List

from plaidapi import (
    AccountBalance,
    AccountInfo,
    Holding,
    InvestmentTransaction,
    Security,
    Transaction as PlaidTransaction,
)


def build_placeholders(list):
    return ",".join(["?"] * len(list))


class TransactionsDB:
    def __init__(self, dbfile: str):
        self.conn = sqlite3.connect(dbfile)

        c = self.conn.cursor()
        c.execute("""
            create table if not exists transactions
                (account_id, transaction_id, created, updated, archived, plaid_json)
            """)
        c.execute(
            "create unique index if not exists accounts_idx     ON transactions(account_id, transaction_id)"
        )
        c.execute(
            "create unique index if not exists transactions_idx ON transactions(transaction_id)"
        )

        c.execute("""
            create table if not exists balances
                (date, item_id, account_id, account_type, balance_current, balance_available, balance_limit, currency_code, updated, plaid_json)
        """)
        c.execute(
            "create unique index if not exists balances_idx ON balances(item_id, account_id, date)"
        )

        c.execute("""
            create table if not exists items
                (item_id, institution_id, consent_expiration, last_failed_update, last_successful_update, updated, plaid_json, cursor)
        """)
        c.execute("create unique index if not exists items_idx ON items(item_id)")

        # Investment activity lives in its own table rather than alongside cash
        # transactions: it is keyed by investment_transaction_id, carries share
        # quantities and prices, and consumers of the cash stream should not have
        # to filter share lots out of it.
        c.execute("""
            create table if not exists investment_transactions
                (account_id, investment_transaction_id, security_id, created, updated, archived, plaid_json)
            """)
        c.execute(
            "create unique index if not exists investment_transactions_idx"
            " ON investment_transactions(investment_transaction_id)"
        )

        # Securities are referenced by id from both investment transactions and
        # holdings, so they are stored once and shared.
        c.execute("""
            create table if not exists securities
                (security_id, ticker_symbol, name, security_type, currency_code, updated, plaid_json)
            """)
        c.execute(
            "create unique index if not exists securities_idx ON securities(security_id)"
        )

        # Point-in-time positions, one row per (account, security, date) so a
        # history builds up as syncs run -- enough to anchor balance assertions
        # for accounts whose transaction history Plaid will not serve.
        c.execute("""
            create table if not exists holdings
                (date, account_id, security_id, quantity, cost_basis, institution_price, institution_value, currency_code, updated, plaid_json)
            """)
        c.execute(
            "create unique index if not exists holdings_idx"
            " ON holdings(account_id, security_id, date)"
        )

        self.conn.commit()

        # This might be needed if there's not consistent support for json_extract in sqlite3 installations
        # this will need to be modified to support the "$.prop" syntax
        # def json_extract(json_str, prop):
        #    ret = json.loads(json_str).get(prop, None)
        #    return ret
        # self.conn.create_function("json_extract", 2, json_extract)

    def datetime_handler(self, obj):
        if isinstance(obj, (datetime.datetime, datetime.date)):
            return obj.isoformat()
        raise TypeError(f"Object of type {type(obj)} is not JSON serializable")

    def get_transaction_ids(
        self, start_date: datetime.date, end_date: datetime.date, account_ids: List[str]
    ) -> List[str]:
        c = self.conn.cursor()
        res = c.execute(
            """
                select transaction_id from transactions
                where json_extract(plaid_json, '$.date') between ? and ?
                and account_id in ({PARAMS})
                and archived is null
            """.replace("{PARAMS}", build_placeholders(account_ids)),
            [start_date.strftime("%Y-%m-%d"), end_date.strftime("%Y-%m-%d")]
            + list(account_ids),
        )
        return [r[0] for r in res.fetchall()]

    def archive_transactions(self, transaction_ids: List[str]):
        c = self.conn.cursor()
        c.execute(
            """
                update transactions set archived = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
                where archived is null
                and transaction_id in ({PARAMS})
                """.replace("{PARAMS}", build_placeholders(transaction_ids)),
            list(transaction_ids),
        )

        self.conn.commit()

    def save_transaction(self, transaction: PlaidTransaction):
        c = self.conn.cursor()
        data = json.dumps(transaction.raw_data, default=self.datetime_handler)
        c.execute(
            """
            insert into
                transactions(account_id, transaction_id, created, updated, archived, plaid_json)
                values(?,?,strftime('%Y-%m-%dT%H:%M:%SZ', 'now'),strftime('%Y-%m-%dT%H:%M:%SZ', 'now'),null,?)
                on conflict(account_id, transaction_id) DO UPDATE
                    set updated    = strftime('%Y-%m-%dT%H:%M:%SZ', 'now'),
                        plaid_json = excluded.plaid_json
        """,
            [transaction.account_id, transaction.transaction_id, data],
        )

        self.conn.commit()

    def save_investment_transaction(self, transaction: InvestmentTransaction):
        c = self.conn.cursor()
        data = json.dumps(transaction.raw_data, default=self.datetime_handler)
        c.execute(
            """
            insert into
                investment_transactions(account_id, investment_transaction_id, security_id, created, updated, archived, plaid_json)
                values(?,?,?,strftime('%Y-%m-%dT%H:%M:%SZ', 'now'),strftime('%Y-%m-%dT%H:%M:%SZ', 'now'),null,?)
                on conflict(investment_transaction_id) DO UPDATE
                    set updated    = strftime('%Y-%m-%dT%H:%M:%SZ', 'now'),
                        security_id = excluded.security_id,
                        plaid_json = excluded.plaid_json
        """,
            [
                transaction.account_id,
                transaction.investment_transaction_id,
                transaction.security_id,
                data,
            ],
        )

        self.conn.commit()

    def save_security(self, security: Security):
        c = self.conn.cursor()
        data = json.dumps(security.raw_data, default=self.datetime_handler)
        c.execute(
            """
            insert into
                securities(security_id, ticker_symbol, name, security_type, currency_code, updated, plaid_json)
                values(?,?,?,?,?,strftime('%Y-%m-%dT%H:%M:%SZ', 'now'),?)
                on conflict(security_id) DO UPDATE
                    set updated       = strftime('%Y-%m-%dT%H:%M:%SZ', 'now'),
                        ticker_symbol = excluded.ticker_symbol,
                        name          = excluded.name,
                        security_type = excluded.security_type,
                        currency_code = excluded.currency_code,
                        plaid_json    = excluded.plaid_json
        """,
            [
                security.security_id,
                security.ticker_symbol,
                security.name,
                security.type,
                security.currency_code,
                data,
            ],
        )

        self.conn.commit()

    def save_holding(self, holding: Holding):
        c = self.conn.cursor()
        data = json.dumps(holding.raw_data, default=self.datetime_handler)
        c.execute(
            """
            insert into
                holdings(date, account_id, security_id, quantity, cost_basis, institution_price, institution_value, currency_code, updated, plaid_json)
                values(strftime('%Y-%m-%d', 'now'),?,?,?,?,?,?,?,strftime('%Y-%m-%dT%H:%M:%SZ', 'now'),?)
                on conflict(account_id, security_id, date) DO UPDATE
                    set updated           = strftime('%Y-%m-%dT%H:%M:%SZ', 'now'),
                        quantity          = excluded.quantity,
                        cost_basis        = excluded.cost_basis,
                        institution_price = excluded.institution_price,
                        institution_value = excluded.institution_value,
                        currency_code     = excluded.currency_code,
                        plaid_json        = excluded.plaid_json
        """,
            [
                holding.account_id,
                holding.security_id,
                holding.quantity,
                holding.cost_basis,
                holding.institution_price,
                holding.institution_value,
                holding.currency_code,
                data,
            ],
        )

        self.conn.commit()

    def save_item_info(self, item_info: AccountInfo):
        c = self.conn.cursor()

        data = json.dumps(item_info.raw_data, default=self.datetime_handler)
        c.execute(
            """
            insert into
                items(item_id, institution_id, consent_expiration, last_failed_update, last_successful_update, updated, plaid_json)
                values(?,?,?,?,?,strftime('%Y-%m-%dT%H:%M:%SZ', 'now'),?)
                on conflict(item_id) DO UPDATE
                    set updated    = strftime('%Y-%m-%dT%H:%M:%SZ', 'now'),
                    institution_id = excluded.institution_id,
                    consent_expiration = excluded.consent_expiration,
                    last_failed_update = excluded.last_failed_update,
                    last_successful_update = excluded.last_successful_update,
                    plaid_json = excluded.plaid_json
        """,
            [
                item_info.item_id,
                item_info.institution_id,
                item_info.ts_consent_expiration,
                item_info.ts_last_failed_update,
                item_info.ts_last_successful_update,
                data,
            ],
        )

        self.conn.commit()

    def save_balance(self, item_id: str, balance: AccountBalance):
        c = self.conn.cursor()

        data = json.dumps(balance.raw_data, default=self.datetime_handler)
        c.execute(
            """
            insert into
                balances(date, item_id, account_id, account_type, balance_current, balance_available, balance_limit, currency_code, updated, plaid_json)
                values(strftime('%Y-%m-%d', 'now'),?,?,?,?,?,?,?,strftime('%Y-%m-%dT%H:%M:%SZ', 'now'), ?)
                on conflict(item_id, account_id, date) DO UPDATE
                    set updated    = strftime('%Y-%m-%dT%H:%M:%SZ', 'now'),
                        account_type = excluded.account_type,
                        balance_current = excluded.balance_current,
                        balance_available = excluded.balance_available,
                        balance_limit = excluded.balance_limit,
                        currency_code = excluded.currency_code,
                        plaid_json = excluded.plaid_json
        """,
            [
                item_id,
                balance.account_id,
                balance.account_type,
                balance.balance_current,
                balance.balance_available,
                balance.balance_limit,
                balance.currency_code,
                data,
            ],
        )

        self.conn.commit()

    def fetch_transactions_by_id(
        self, transaction_ids: List[str]
    ) -> List[PlaidTransaction]:
        c = self.conn.cursor()
        r = c.execute(
            """
            select plaid_json from transactions
            where transaction_id in ({PARAMS})
        """.replace("{PARAMS}", build_placeholders(transaction_ids)),
            list(transaction_ids),
        )
        return [PlaidTransaction(json.loads(d[0])) for d in r.fetchall()]

    def get_last_sync_cursor(self, item_id):
        """
        Retrieve the last sync cursor for a given item_id.
        Returns None if no cursor exists (indicating initial sync needed).
        """
        c = self.conn.cursor()
        cursor = c.execute(
            "SELECT cursor FROM items WHERE item_id = ?", (item_id,)
        ).fetchone()

        if cursor and cursor[0]:
            return cursor[0]
        return None

    def save_sync_cursor(self, item_id, cursor):
        """
        Save the sync cursor for a given item_id.
        Updates the existing item record with the new cursor.
        """
        c = self.conn.cursor()
        c.execute("UPDATE items SET cursor = ? WHERE item_id = ?", (cursor, item_id))
        self.conn.commit()
