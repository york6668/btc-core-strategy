"""Public Binance report subjects; never infer a product or holding from an index."""
from copy import deepcopy
from decimal import Decimal
from .common import exact, instant, require, text

UNDERLYING = {'QQQ': 'NDX', 'VOO': 'SPX'}
REPORT_QUOTE_POLICY = {'revision': 2, 'venue': 'BINANCE', 'quote_unit': 'USD',
                       'accepted_quote_units': ['USD', 'USD/share', 'USDT'],
                       'valuation_basis': 'APPROXIMATE_USD_USDT', 'show_underlying_prices': False}
INSTRUCTIONS = (
    '用户希望报告覆盖 BTC 及拟核对的币安 QQQ、VOO 产品；SPX/NDX 保留为底层市场研究及历史区域身份。'
    '报告目标场所不等于产品身份已核验；QQQ/VOO 名称不证明具体产品版本，仅用已核验公开产品身份、报价单位、时间及区域。'
    '不得假定 QQQB、QQQon 等版本，不把指数点位改名或折算为产品价格。'
    '产品报价缺失仅限制产品价位判断；方向、盈利价值及组合应对继续使用底层研究并说明依据。'
    '产品区域独立建立，与底层区域的关系由证据说明，不能覆盖指数区域；表达变化不调整持仓。'
    '用户采用美元近似口径，USD与USDT常规微差不阻断分析或价位展示，保留原始币种、每股单位和产品身份。'
    '股票USD/股、合约USDT分别展示；支付USDC不代表报价USDC，不拼造交易对。底层指数点位保留研究用途。'
    '源时间缺失时如实保留未知并单列抓取时间，不以抓取时间冒充行情时刻或确认触价。'
)


def validate(records, bundle, directory):
    """Optional evidence extension. Every quote binds raw instrument/unit/time/value."""
    from .evidence import resolve, resolve_number, canonical_origin
    require(isinstance(records, list), 'report products must be a list')
    seen = set()
    sources = {s['id']: s for s in bundle['sources']}
    for row in records:
        exact(row, 'symbol venue product_id product_type quote_unit identity_refs quote', 'public report product')
        require(row['symbol'] in UNDERLYING and row['symbol'] not in seen, 'ambiguous report product')
        seen.add(row['symbol'])
        require(row['venue'] == 'BINANCE', 'report product needs Binance provenance')
        for key in ('product_id', 'product_type', 'quote_unit'):
            text(row[key], 'product ' + key)
        require(row['product_id'] not in UNDERLYING.values(), 'underlying index cannot be a product identity')
        require(row['quote_unit'] in ('USD', 'USD/share', 'USDT', 'USDC'), 'product price cannot use index points')
        refs = row['identity_refs']
        exact(refs, 'symbol product_id product_type quote_unit', 'product identity references')
        for key, ref in refs.items():
            require(resolve(bundle, directory, ref, verified=True) == row[key], 'product identity differs from public raw field')
        require(any(canonical_origin(sources[r['source_id']]['url']) == 'BINANCE' for r in refs.values()),
                'Binance product identity requires a Binance public source')
        quote = row['quote']
        if quote is None:
            continue
        extended = 'retrieved_at' in quote or 'time_basis' in quote
        exact(quote, 'price as_of refs' + (' retrieved_at time_basis' if extended else ''), 'public product quote')
        unknown_time = extended and quote['as_of'] is None
        exact(quote['refs'], 'price product_id quote_unit' + ('' if unknown_time else ' as_of'), 'product quote references')
        resolved = {k: resolve(bundle, directory, r, verified=True) for k, r in quote['refs'].items()}
        require(resolved['product_id'] == row['product_id'] and resolved['quote_unit'] == row['quote_unit'],
                'quote instrument or unit differs from verified product')
        # A quote must be one instrument row, not a number borrowed from another source.
        prefixes = {(r['source_id'], r['pointer'].rsplit('/', 1)[0]) for r in quote['refs'].values()}
        require(len(prefixes) == 1, 'quote fields must belong to the same raw instrument row')
        price = Decimal(str(quote['price']))
        require(price.is_finite() and price > 0 and price == Decimal(resolve_number(bundle, directory, quote['refs']['price'], verified=True)),
                'product price differs from public raw quote')
        if not unknown_time:
            require(resolved['as_of'] == quote['as_of'] and instant(quote['as_of']) <= instant(bundle['cutoff']),
                    'product quote time differs from public evidence')
        if extended:
            owner = sources[quote['refs']['price']['source_id']]
            require(quote['retrieved_at'] == owner['retrieved_at'], 'quote retrieval time differs from frozen source')
            require(quote['time_basis'] == ('RETRIEVED_AT_SOURCE_TIME_UNKNOWN' if unknown_time else 'SOURCE_OBSERVATION'),
                    'quote time basis differs from available source time')
            if not unknown_time:
                require(instant(quote['as_of']) <= instant(quote['retrieved_at']), 'quote source time exceeds retrieval')


def subjects(bundle=None, zones=()):
    """Expose only frozen public evidence, including explicit unavailable prices."""
    rows = {r['symbol']: r for r in (bundle or {}).get('report_products', [])}
    result = {}
    for symbol, underlying in UNDERLYING.items():
        row = rows.get(symbol)
        for zone in zones:
            if row and zone['market'] == row['product_id']:
                require(zone['quote_unit'] == row['quote_unit'], 'product zone quote unit differs from verified product')
        result[symbol] = {
            'symbol': symbol, 'venue': 'BINANCE', 'underlying_market': underlying,
            'report_quote_unit': row['quote_unit'] if row else REPORT_QUOTE_POLICY['quote_unit'],
            'identity_status': 'VERIFIED' if row else 'UNVERIFIED',
            'product_id': row['product_id'] if row else None,
            'product_type': row['product_type'] if row else None,
            'quote_unit': row['quote_unit'] if row else None,
            'quote': deepcopy(row['quote']) if row else None,
            'identity_refs': deepcopy(row['identity_refs']) if row else {},
            'product_zones': [deepcopy(z) for z in zones if row and z['market'] == row['product_id']],
            'underlying_zones': [deepcopy(z) for z in zones if z['market'] == underlying],
            'limitation': ('报价源时间未提供；抓取时间单列，不能用于确认精确触价时刻。' if row and row['quote']
                           and row['quote_unit'] in REPORT_QUOTE_POLICY['accepted_quote_units'] and row['quote']['as_of'] is None
                           else None if row and row['quote'] and row['quote_unit'] in REPORT_QUOTE_POLICY['accepted_quote_units']
                           else '对应产品的公开报价尚未核验；仅限制产品价位，不阻断底层市场分析。'),
        }
    return result


def retained(packages, source_ids):
    """Preserve a product only with its complete public reference closure."""
    chosen = {}
    for package in packages:
        for row in package.get('report_products', []):
            refs = list(row['identity_refs'].values()) + list((row['quote'] or {}).get('refs', {}).values())
            if all(r['source_id'] in source_ids for r in refs):
                chosen[row['symbol']] = deepcopy(row)
    return list(chosen.values())
