"""Deterministic source observations, never model-authored market conclusions.

The original response remains the evidence owner. Derived references are
recomputed from its bytes; forecasts and unverifiable legacy extracts cannot
become prospective observations merely by declaring a kind or value.
"""
from __future__ import annotations

import base64
import calendar
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser
from io import BytesIO
import re
from urllib.parse import urlsplit, parse_qs, unquote
import xml.etree.ElementTree as ET

from .common import digest, instant, require

PREFIX = '/$facts/'


def document_transport(payload):
    """Reversible JSON transport; extraction is deliberately not accepted here."""
    require(isinstance(payload, bytes), 'document transport requires original bytes')
    return {'transport': 'ORIGINAL_BYTES_BASE64', 'original_sha256': digest(payload)[7:],
            'bytes_base64': base64.b64encode(payload).decode('ascii')}


def _document(raw):
    if not isinstance(raw, dict) or raw.get('transport') != 'ORIGINAL_BYTES_BASE64':
        return None
    try:
        payload = base64.b64decode(raw['bytes_base64'], validate=True)
    except (ValueError, TypeError, KeyError):
        require(False, 'invalid original document byte transport')
    require(digest(payload)[7:] == raw.get('original_sha256'), 'original document hash mismatch')
    if 'literal_text' in raw:
        require(raw['literal_text'] == payload.decode('utf-8'), 'literal text differs from original document bytes')
    return payload


class _Text(HTMLParser):
    def __init__(self):
        super().__init__(); self.parts = []; self.skip = 0; self.rows = []; self.row = None; self.cell = None
        self.tables = []; self.table_stack = []; self.row_simple = True

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style', 'noscript'): self.skip += 1
        if tag == 'table':
            table = []; self.tables.append(table); self.table_stack.append(table)
        if tag == 'tr': self.row = []; self.row_simple = True
        if tag in ('td', 'th') and any(key in ('colspan', 'rowspan') and value != '1' for key, value in attrs):
            self.row_simple = False
        if tag in ('td', 'th'): self.cell = []
        if tag in ('p', 'div', 'li', 'tr', 'br', 'h1', 'h2', 'h3'): self.parts.append(' ')

    def handle_endtag(self, tag):
        if tag in ('script', 'style', 'noscript') and self.skip: self.skip -= 1
        if tag in ('td', 'th') and self.cell is not None:
            if self.row is not None: self.row.append(' '.join(''.join(self.cell).split()))
            self.cell = None
        if tag == 'tr' and self.row is not None:
            self.rows.append(self.row)
            if self.table_stack: self.table_stack[-1].append((len(self.rows) - 1, self.row, self.row_simple))
            self.row = None
        if tag == 'table' and self.table_stack: self.table_stack.pop()
        if tag in ('p', 'div', 'li', 'tr', 'td', 'th'): self.parts.append(' ')

    def handle_data(self, value):
        if not self.skip:
            self.parts.append(value)
            if self.cell is not None: self.cell.append(value)


def _plain(payload):
    parser = _Text(); parser.feed(payload.decode('utf-8'))
    return ' '.join(''.join(parser.parts).split()), parser.rows


def _date(value):
    if isinstance(value, str) and len(value) == 10: value += 'T00:00:00Z'
    return instant(value).isoformat()


def _month_period(year, month):
    return (f'{year:04d}-{month:02d}-01T00:00:00Z',
            f'{year:04d}-{month:02d}-{calendar.monthrange(year, month)[1]:02d}T23:59:59Z')


def _record(source, key, value, scope, period, quote, location, kind='EXTERNAL_OBSERVATION', available_at=None):
    """Economic event end and source availability are independent clocks."""
    start, end = map(_date, period)
    observed = end
    require(instant(source['start']) <= instant(start) <= instant(end) <= instant(source['end']),
            'derived observation period exceeds its original frozen source')
    available = available_at or source.get('published_at')
    if available is not None:
        available = _date(available)
        require(instant(available) <= instant(source['retrieved_at']), 'source publication exceeds retrieval')
        require(instant(end) <= instant(available), 'realized observation cannot predate its economic period')
    return {'id': key, 'kind': kind, 'value': value, 'scope': scope,
            'economic_period': {'start': start, 'end': end}, 'observed_at': observed,
            'available_at': available, 'known_at': source['retrieved_at'],
            'provenance': {'source_id': source['id'], 'raw_hash': source['raw_hash'],
                           'url': source['url'], 'literal': quote, 'location': location,
                           'derivation': 'DETERMINISTIC_SOURCE_EXTRACTION_V1'}}


def _nasdaq(source, payload):
    text, _ = _plain(payload)
    # Preserve the provider's approximate/bounded statement, not an invented
    # exact 80% or a mislabeled EPS-surprise statistic.
    period = re.search(r'\bsecond[- ]quarter(?: earnings)?\b', text, re.I)
    year = re.search(r'earnings growth in (\d{4})', text)
    breadth = re.search(r'More than four in five reported positive earnings growth', text)
    if not (period and year and breadth): return []
    start, end = str(year[1]) + '-04-01T00:00:00Z', str(year[1]) + '-06-30T23:59:59Z'
    scope = {'subject': 'NDX', 'product': 'INDEX_CONSTITUENTS', 'universe': 'NASDAQ100_REPORTING_COMPANIES',
             'measure': 'POSITIVE_EARNINGS_GROWTH_BREADTH', 'unit': 'SOURCE_STATEMENT',
             'period_kind': 'REPORTING_QUARTER', 'event_type': 'ECONOMIC_ACTIVITY', 'period': str(year[1]) + '-Q2'}
    return [_record(source, 'nasdaq:' + str(year[1]) + '-Q2:positive_growth_breadth', breadth[0], scope,
        (start, end), breadth[0], '/html/text/earnings_breadth')]


def _number(value):
    token = value.strip().replace(',', '')
    if token.startswith('(') and token.endswith(')'): token = '-' + token[1:-1]
    try: result = Decimal(token)
    except InvalidOperation: return None
    return str(result) if result.is_finite() else None


def _treasury(source, payload):
    query = parse_qs(urlsplit(source['url']).query)
    mode = query.get('data', [None])[0]
    if mode not in ('daily_treasury_yield_curve', 'daily_treasury_real_yield_curve'): return []
    real = mode == 'daily_treasury_real_yield_curve'
    root = ET.fromstring(payload); records = []
    for entry in root.iter():
        if entry.tag.split('}')[-1] != 'properties': continue
        row = {el.tag.split('}')[-1]: el.text for el in entry}
        if not row.get('NEW_DATE'): continue
        stamp = _date(row['NEW_DATE'] + 'Z' if len(row['NEW_DATE']) == 19 else row['NEW_DATE'])
        for field, value in row.items():
            prefix = 'TC_' if real else 'BC_'
            maturities = ('5YEAR', '7YEAR', '10YEAR', '20YEAR', '30YEAR') if real else (
                '1MONTH', '1_5MONTH', '2MONTH', '3MONTH', '4MONTH', '6MONTH', '1YEAR',
                '2YEAR', '3YEAR', '5YEAR', '7YEAR', '10YEAR', '20YEAR', '30YEAR')
            if field not in {prefix + maturity for maturity in maturities} or value is None: continue
            numeric = _number(value)
            scope = {'subject': 'US_TREASURY', 'product': 'BENCHMARK_YIELD_CURVE',
                     'measure': 'REAL_YIELD' if real else 'NOMINAL_YIELD',
                     'unit': 'ANNUAL_PERCENT', 'maturity': field[len(prefix):], 'period_kind': 'POINT', 'period': stamp[:10]}
            records.append(_record(source, 'treasury:' + stamp[:10] + ':' + field,
                numeric, scope, (stamp, stamp), value, '/properties/NEW_DATE=' + row['NEW_DATE'] + '/' + field))
    return records


def _stablecoins(source, raw):
    if not isinstance(raw, list): return []
    records = []
    for index, row in enumerate(raw):
        if not isinstance(row, dict) or not str(row.get('date', '')).isdigit(): continue
        stamp = datetime.fromtimestamp(int(row['date']), timezone.utc).isoformat()
        if not instant(source['start']) <= instant(stamp) <= instant(source['end']): continue
        values = row.get('totalCirculatingUSD', {})
        if not isinstance(values, dict): continue
        for peg, value in values.items():
            scope = {'subject': 'STABLECOINS', 'product': peg, 'universe': 'DEFILLAMA_TRACKED',
                     'measure': 'CIRCULATING_CAPITALIZATION', 'unit': 'USD', 'period_kind': 'POINT', 'period': stamp[:10]}
            records.append(_record(source, 'stablecoins:' + stamp[:10] + ':' + peg,
                value, scope, (stamp, stamp), str(value), f'/{index}/totalCirculatingUSD/{peg}'))
    return records


def _yahoo(source, raw):
    if not isinstance(raw, dict) or raw.get('kind') in ('FORECAST', 'HYPOTHESIS', 'SIMULATION', 'BACKTEST'): return []
    chart = raw.get('chart', {})
    if not isinstance(chart, dict) or chart.get('error') or not isinstance(chart.get('result'), list): return []
    requested = unquote(urlsplit(source['url']).path.rsplit('/', 1)[-1])
    identities = {'^GSPC': ('SPX', 'INDEX', {'SNP'}), '^NDX': ('NDX', 'INDEX', {'NIM'}),
                  'QQQ': ('QQQ', 'ETF', {'NGM', 'NMS', 'NCM'}),
                  'VOO': ('VOO', 'ETF', {'PCX'}), 'GLD': ('GLD', 'ETF', {'PCX'})}
    records = []
    for result_index, result in enumerate(chart['result']):
        if not isinstance(result, dict): continue
        meta = result.get('meta', {}); symbol = meta.get('symbol')
        if symbol not in identities or requested != symbol or meta.get('currency') != 'USD': continue
        subject, product, exchanges = identities[symbol]
        if meta.get('instrumentType') != product or meta.get('exchangeName') not in exchanges or meta.get('dataGranularity') != '1d': continue
        if meta.get('kind') in ('FORECAST', 'HYPOTHESIS', 'SIMULATION', 'BACKTEST'): continue
        quotes = result.get('indicators', {}).get('quote', [])
        stamps = result.get('timestamp', [])
        if not isinstance(quotes, list) or len(quotes) != 1 or not isinstance(quotes[0], dict): continue
        closes = quotes[0].get('close', [])
        if not isinstance(stamps, list) or not isinstance(closes, list) or len(stamps) != len(closes): continue
        # A daily timestamp is the session start. Only response-provided
        # session clocks can establish the close; no fixed 16:00 rule or
        # 24-hour addition fabricates half-day/historical session boundaries.
        periods = []
        current = meta.get('currentTradingPeriod', {}).get('regular')
        if isinstance(current, dict): periods.append(current)
        trading = result.get('tradingPeriods', {})
        if isinstance(trading, dict):
            for group in trading.get('regular', []):
                if isinstance(group, list): periods.extend(p for p in group if isinstance(p, dict))
                elif isinstance(group, dict): periods.append(group)
        for index, stamp in enumerate(stamps):
            if not isinstance(stamp, int): continue
            period = next((p for p in periods if p.get('start') == stamp and isinstance(p.get('end'), int)), None)
            if period is None: continue
            start = datetime.fromtimestamp(stamp, timezone.utc).isoformat()
            end = datetime.fromtimestamp(period['end'], timezone.utc).isoformat()
            if not instant(source['start']) <= instant(start) < instant(end) <= instant(source['end']): continue
            if instant(end) > instant(source['retrieved_at']): continue
            value = closes[index]
            if value is not None and not isinstance(value, (int, float, str)): continue
            numeric = _number(str(value)) if value is not None else None
            scope = {'subject': subject, 'symbol': symbol, 'venue': meta['exchangeName'], 'product': product,
                     'measure': 'PRICE', 'quote_unit': 'USD', 'unit': 'INDEX_POINTS' if product == 'INDEX' else 'USD_PER_SHARE',
                     'period_kind': 'DAILY_PRICE_BAR', 'period': start[:10]}
            records.append(_record(source, 'yahoo:' + symbol + ':' + start[:10] + ':close', numeric, scope,
                (start, end), str(value), f'/chart/result/{result_index}/indicators/quote/0/close/{index}'))
    return records


# Named membership of the admitted Farside table schema, not a column count.
# An omitted known column cannot silently narrow FARSIDE_TRACKED. Additional
# named fund columns are included in every row's completeness and sum checks.
_FARSIDE_FUNDS = frozenset('IBIT FBTC BITB ARKB BTCO EZBC BRRR HODL BTCW MSBT GBTC BTC'.split())


def _farside(source, payload):
    parser = _Text(); parser.feed(payload.decode('utf-8'))
    text = ' '.join(''.join(parser.parts).split())
    if 'Bitcoin ETF Flow (US$m)' not in text: return []
    records = []
    for table in parser.tables:
        headers = None
        for index, row, simple in table:
            names = [cell.upper() for cell in row]
            if 'DATE' in names or 'TOTAL' in names:
                # The provider also uses an empty first date-column heading.
                if names and names[0] == '': names[0] = 'DATE'
                headers = names if (simple and 'DATE' in names and len(set(names)) == len(names)
                    and all(re.fullmatch(r'[A-Z][A-Z0-9.\-]*', name) for name in names)
                    and any(name not in ('DATE', 'TOTAL') for name in names)) else None
                continue
            if headers is None or not simple or len(row) != len(headers): continue
            cells = dict(zip(headers, row))
            try: day = datetime.strptime(cells['DATE'], '%d %b %Y').date().isoformat()
            except ValueError: continue
            start = day + 'T00:00:00Z'; end = day + 'T23:59:59Z'
            if not instant(source['start']) <= instant(start) <= instant(end) <= instant(source['end']): continue
            constituents = {name: _number(value) for name, value in cells.items() if name not in ('DATE', 'TOTAL')}
            missing_columns = sorted(_FARSIDE_FUNDS - constituents.keys())
            if 'TOTAL' not in cells: missing_columns.append('Total')
            missing_values = sorted(name for name, value in constituents.items() if value is None)
            total = _number(cells['TOTAL']) if 'TOTAL' in cells else None
            if 'TOTAL' in cells and total is None: missing_values.append('Total')
            # Never reconstruct an absent total or turn a reported subtotal
            # into a full-universe fact. Inconsistent totals remain unknown.
            matches = (sum((Decimal(value) for value in constituents.values()), Decimal(0)) == Decimal(total)
                       if not missing_columns and not missing_values else None)
            complete = matches is True
            scope = {'subject': 'BTC', 'product': 'US_SPOT_ETF', 'universe': 'FARSIDE_TRACKED',
                     'measure': 'NET_FUND_FLOW', 'unit': 'USD_MILLION', 'period_kind': 'DAILY_AGGREGATE', 'period': day}
            record = _record(source, 'farside:' + day + ':total', total if complete else None, scope,
                (start, end), ' | '.join(row), '/html/table/row/' + str(index) + '/Total')
            record['coverage'] = {'status': 'COMPLETE' if complete else 'INCOMPLETE',
                'constituents': constituents, 'missing_columns': missing_columns, 'missing_values': missing_values,
                'reported_total': total, 'total_matches_constituents': matches}
            records.append(record)
    return records


def _wgc(source, payload):
    text, _ = _plain(payload)
    title = re.search(r'Gold ETF Flows: ([A-Za-z]+) (\d{4})', text)
    if not title: return []
    month = datetime.strptime(title[1], '%B').month; year = int(title[2])
    period = _month_period(year, month)
    publication = re.search(r'<time\b[^>]*datetime=["\']([^"\']+)', payload.decode('utf-8'))
    available = publication[1] if publication else None
    subject = r'Global physically-backed gold ETFs\s*\d*\s+'
    specs = [
        ('fund_flow', subject + r'(?:attracted|saw (?:net )?inflows of) US\$([\d.,]+)bn in ' + re.escape(title[1]),
         'NET_FUND_FLOW', 'USD_BILLION', 1),
        ('fund_flow', subject + r'(?:lost|saw (?:net )?outflows of) US\$([\d.,]+)bn in ' + re.escape(title[1]),
         'NET_FUND_FLOW', 'USD_BILLION', -1),
        ('holdings_change', r'global holdings (?:rose|by) ([\d.,]+)t', 'HOLDINGS_CHANGE', 'TONNES', 1),
        ('holdings_change', r'global holdings (?:fell|declined)(?: by)? ([\d.,]+)t', 'HOLDINGS_CHANGE', 'TONNES', -1),
        ('holdings', r'global holdings (?:rose|fell|declined)(?: by)? [\d.,]+t to (?:a record )?([\d.,]+)t',
         'HOLDINGS', 'TONNES', 1),
    ]
    records = []
    for key, grammar, measure, unit, sign in specs:
        match = re.search(grammar, text, re.I)
        if not match: continue
        scope = {'subject': 'GOLD', 'product': 'PHYSICALLY_BACKED_ETF', 'universe': 'WGC_GLOBAL',
                 'measure': measure, 'unit': unit, 'period_kind': 'CALENDAR_MONTH', 'period': f'{year:04d}-{month:02d}'}
        value = _number(match[1])
        if value is not None and sign < 0: value = str(-Decimal(value))
        records.append(_record(source, 'wgc:' + f'{year:04d}-{month:02d}:' + key,
            value, scope, period, match[0], '/html/text/' + key, available_at=available))
    return records


def _factset(source, payload):
    # Extraction is repeated from original PDF bytes. An attached model/text
    # projection, even with a hash or kind, is never a replacement for this.
    try:
        from pypdf import PdfReader
    except ImportError:
        return []
    reader = PdfReader(BytesIO(payload))
    text = ' '.join(' '.join((page.extract_text() or '').split()) for page in reader.pages)
    date_match = re.search(r'\b([A-Za-z]+ \d{1,2}, \d{4})\b', text[:2000])
    if not date_match: return []
    day = datetime.strptime(date_match[1], '%B %d, %Y').date().isoformat()
    stamp = day + 'T00:00:00Z'
    score = re.search(r'For Q([1-4]) (\d{4}) \(with (\d+) S&P 500 companies reporting actual results\), '
                      r'(\d+) S&P 500 companies have reported a positive EPS surprise and '
                      r'(\d+) S&P 500 companies (?:has|have) reported a positive revenue surprise', text)
    records = []
    if score:
        period = score[2] + '-Q' + score[1]
        for key, group, measure in (('reporters', 3, 'ACTUAL_REPORTER_COUNT'),
                ('eps_surprises', 4, 'POSITIVE_EPS_SURPRISE_COUNT'), ('revenue_surprises', 5, 'POSITIVE_REVENUE_SURPRISE_COUNT')):
            scope = {'subject': 'SPX', 'product': 'INDEX_CONSTITUENTS', 'universe': 'FACTSET_REPORTING_COMPANIES',
                     'measure': measure, 'unit': 'COMPANIES', 'period_kind': 'REPORTING_QUARTER',
                     'event_type': 'DISCLOSURE_COUNT_SNAPSHOT', 'period': period, 'as_of': day}
            records.append(_record(source, 'factset:' + day + ':' + key, score[group], scope,
                (stamp, stamp), score[0], '/pdf/earnings_scorecard/' + key))
    estimate = re.search(r'For Q([1-4]) (\d{4}), the estimated \(year-over-year\) earnings growth rate '
                         r'for the S&P 500 is ([\d.,]+)%', text)
    if estimate:
        scope = {'subject': 'SPX', 'product': 'INDEX_CONSTITUENTS', 'universe': 'FACTSET_SP500',
                 'measure': 'ESTIMATED_EARNINGS_GROWTH', 'unit': 'PERCENT_YOY',
                 'period_kind': 'REPORTING_QUARTER', 'event_type': 'ESTIMATE_SNAPSHOT',
                 'period': estimate[2] + '-Q' + estimate[1], 'as_of': day}
        records.append(_record(source, 'factset:' + day + ':estimated_growth', estimate[3], scope,
            (stamp, stamp), estimate[0], '/pdf/earnings_growth_estimate', kind='FORECAST'))
    return records


def records(source, raw):
    """Only recognized response grammars produce classified derived facts."""
    endpoint = urlsplit(source['url']); host = endpoint.hostname or ''; path = endpoint.path
    payload = _document(raw)
    if host == 'stablecoins.llama.fi' and path == '/stablecoincharts/all':
        result = _stablecoins(source, raw)
    elif host in ('query1.finance.yahoo.com', 'query2.finance.yahoo.com') and path.startswith('/v8/finance/chart/'):
        result = _yahoo(source, raw)
    elif host == 'home.treasury.gov' and path.endswith('/interest-rates/pages/xml') and payload is not None:
        result = _treasury(source, payload)
    elif host == 'farside.co.uk' and path == '/btc/' and payload is not None:
        result = _farside(source, payload)
    elif host in ('www.gold.org', 'gold.org') and '/gold-etfs-holdings-and-flows/' in path and payload is not None:
        result = _wgc(source, payload)
    elif host == 'advantage.factset.com' and '/EarningsInsight_' in path and path.endswith('.pdf') and payload is not None:
        result = _factset(source, payload)
    elif host in ('www.nasdaq.com', 'nasdaq.com') and path.startswith('/economic-institute/the-print/') and payload is not None:
        result = _nasdaq(source, payload)
    else:
        result = []
    require(len({r['id'] for r in result}) == len(result), 'ambiguous duplicate observations in source')
    return result


def projection(source, raw, record_id):
    result = next((r for r in records(source, raw) if r['id'] == record_id), None)
    require(result is not None, 'derived observation absent or its source parser is unavailable')
    return result
