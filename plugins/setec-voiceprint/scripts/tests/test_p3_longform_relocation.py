"""P3 long-form compatibility in copied plugins and fresh stdlib processes.

Old payloads are genuine baseline-generated synthetic pickle fixtures. New
payloads load through cold legacy imports. No Git history, installed backends,
source comparisons, or implementation hashes are needed to run these tests.
"""

import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest


PLUGIN_ROOT = Path(__file__).resolve().parents[2]
MODULES = ("narrative_longform_segment", "storyscope_polarity_contract")


# Generated on Python 3.12 from the unchanged narrative_longform_segment at
# base 7537b9f64af8181e1b59250ce66bdfc150d5ac06, using make_graph() below.
# Base64 encodes actual protocol 0-5 bytes, including records, class globals,
# an exception, shared references and cycles. The baseline source is not vendored.
_BASELINE_PICKLES = {
    0: (
        "KGRwMApWcmVjb3JkCnAxCmNjb3B5X3JlZwpfcmVjb25zdHJ1Y3RvcgpwMgooY25hcnJhdGl2ZV9sb25nZm9ybV9z"
        "ZWdtZW50ClNlZ21lbnRhdGlvbgpwMwpjX19idWlsdGluX18Kb2JqZWN0CnA0Ck50cDUKUnA2CihkcDcKVnNlZ21l"
        "bnRlcl92ZXJzaW9uCnA4ClZuYXJyYXRpdmUtbG9uZ2Zvcm0tc2VnbWVudGVyLzEKcDkKc1Z0aWVyCnAxMApWY2hh"
        "cHRlcl9oZWFkaW5nCnAxMQpzVnNlZ21lbnRfdGFyZ2V0X3dvcmRzCnAxMgpJNTAwMApzVnNlZ21lbnRzCnAxMwoo"
        "ZzIKKGNuYXJyYXRpdmVfbG9uZ2Zvcm1fc2VnbWVudApTZWdtZW50CnAxNApnNApOdHAxNQpScDE2CihkcDE3ClZp"
        "bmRleApwMTgKSTAKc1ZzdGFydApwMTkKSTAKc1ZlbmQKcDIwCkkxODAxNApzVm5fd29yZHMKcDIxCkkzMDAyCnNW"
        "Y29udGVudF9zaGEyNTYKcDIyClZzaGEyNTY6MTYxYzg0YmEwNDU4NDNhNDM0MGM0ZmMxZTJlYTNhMjRmMmQ1ZWU0"
        "NWFlNDJlMzQxNmE2ZmM1NDAyOTlmZjI4NgpwMjMKc2JnMgooZzE0Cmc0Ck50cDI0ClJwMjUKKGRwMjYKZzE4Ckkx"
        "CnNnMTkKSTE4MDE0CnNnMjAKSTM2NjQyCnNnMjEKSTMxMDQKc2cyMgpWc2hhMjU2OjUzYjE3NDJhOGYyN2M0NDBh"
        "YzU3NmVlYzJhYzI1MzE0NDI1YmU0MmExZTlmYzZkYzM5NTliNDMwZGI2NWViMWQKcDI3CnNidHAyOApzVmV4Y2x1"
        "ZGVkX3NwYW5zCnAyOQooKGRwMzAKVnJlYXNvbgpwMzEKVnN5bnRoZXRpYyBzaGFyZWQgbWV0YWRhdGEKcDMyCnNn"
        "MjEKSTAKc1ZncmFwaApwMzMKZzAKc2czMAp0cDM0CnNWcGFyYW1zX3NoYTI1NgpwMzUKVnNoYTI1NjpmYjZmZjc1"
        "NmViZmU0YmYwNjg5ODBiYzg2MGM5NzUxN2U4YzA1ZWMxYjI1ZjE1NGQzNDQ4OGUwZGM2MzFiZGRjCnAzNgpzVmJv"
        "dW5kYXJ5X29mZnNldHNfc2hhMjU2CnAzNwpWc2hhMjU2OjQ5ZWNhZmU2NjIyMWNjZDk2ZWMzMWRkNjIyYTE4MDdm"
        "NzM2MWJmNzQyYTQ5N2JiNWFmMWRmYTk4MzI3MWY1MTkKcDM4CnNic1ZyZWNvcmRfYWxpYXMKcDM5Cmc2CnNWc2Vn"
        "bWVudApwNDAKZzE2CnNWc2VnbWVudF9hbGlhcwpwNDEKZzE2CnNWZXJyb3IKcDQyCmNuYXJyYXRpdmVfbG9uZ2Zv"
        "cm1fc2VnbWVudApTZWdtZW50YXRpb25JbmZlYXNpYmxlCnA0MwooVmVtcHR5IG9yIHdoaXRlc3BhY2Utb25seSB0"
        "ZXh0CnA0NAp0cDQ1ClJwNDYKKGRwNDcKZzMzCmcwCnNic1ZlcnJvcl9hbGlhcwpwNDgKZzQ2CnNWY2xhc3Nlcwpw"
        "NDkKKGcxNApnMwpnNDMKdHA1MApzVnByb2plY3Rpb24KcDUxCihkcDUyCmc4Cmc5CnNnMTAKZzExCnNnMTIKSTUw"
        "MDAKc2czNQpnMzYKc2czNwpnMzgKc1ZuX3NlZ21lbnRzCnA1MwpJMgpzZzEzCihscDU0CihkcDU1CmcxOApJMApz"
        "ZzIxCkkzMDAyCnNnMjIKZzIzCnNhKGRwNTYKZzE4CkkxCnNnMjEKSTMxMDQKc2cyMgpnMjcKc2FzZzI5CihscDU3"
        "CihkcDU4CmczMQpnMzIKc2cyMQpJMApzYShkcDU5CmczMQpnMzIKc2cyMQpJMApzYXNzVnNlbGYKcDYwCmcwCnMu"
    ),
    1: (
        "fXEAKFgGAAAAcmVjb3JkcQFjY29weV9yZWcKX3JlY29uc3RydWN0b3IKcQIoY25hcnJhdGl2ZV9sb25nZm9ybV9z"
        "ZWdtZW50ClNlZ21lbnRhdGlvbgpxA2NfX2J1aWx0aW5fXwpvYmplY3QKcQROdHEFUnEGfXEHKFgRAAAAc2VnbWVu"
        "dGVyX3ZlcnNpb25xCFgeAAAAbmFycmF0aXZlLWxvbmdmb3JtLXNlZ21lbnRlci8xcQlYBAAAAHRpZXJxClgPAAAA"
        "Y2hhcHRlcl9oZWFkaW5ncQtYFAAAAHNlZ21lbnRfdGFyZ2V0X3dvcmRzcQxNiBNYCAAAAHNlZ21lbnRzcQ0oaAIo"
        "Y25hcnJhdGl2ZV9sb25nZm9ybV9zZWdtZW50ClNlZ21lbnQKcQ5oBE50cQ9ScRB9cREoWAUAAABpbmRleHESSwBY"
        "BQAAAHN0YXJ0cRNLAFgDAAAAZW5kcRRNXkZYBwAAAG5fd29yZHNxFU26C1gOAAAAY29udGVudF9zaGEyNTZxFlhH"
        "AAAAc2hhMjU2OjE2MWM4NGJhMDQ1ODQzYTQzNDBjNGZjMWUyZWEzYTI0ZjJkNWVlNDVhZTQyZTM0MTZhNmZjNTQw"
        "Mjk5ZmYyODZxF3ViaAIoaA5oBE50cRhScRl9cRooaBJLAWgTTV5GaBRNIo9oFU0gDGgWWEcAAABzaGEyNTY6NTNi"
        "MTc0MmE4ZjI3YzQ0MGFjNTc2ZWVjMmFjMjUzMTQ0MjViZTQyYTFlOWZjNmRjMzk1OWI0MzBkYjY1ZWIxZHEbdWJ0"
        "cRxYDgAAAGV4Y2x1ZGVkX3NwYW5zcR0ofXEeKFgGAAAAcmVhc29ucR9YGQAAAHN5bnRoZXRpYyBzaGFyZWQgbWV0"
        "YWRhdGFxIGgVSwBYBQAAAGdyYXBocSFoAHVoHnRxIlgNAAAAcGFyYW1zX3NoYTI1NnEjWEcAAABzaGEyNTY6ZmI2"
        "ZmY3NTZlYmZlNGJmMDY4OTgwYmM4NjBjOTc1MTdlOGMwNWVjMWIyNWYxNTRkMzQ0ODhlMGRjNjMxYmRkY3EkWBcA"
        "AABib3VuZGFyeV9vZmZzZXRzX3NoYTI1NnElWEcAAABzaGEyNTY6NDllY2FmZTY2MjIxY2NkOTZlYzMxZGQ2MjJh"
        "MTgwN2Y3MzYxYmY3NDJhNDk3YmI1YWYxZGZhOTgzMjcxZjUxOXEmdWJYDAAAAHJlY29yZF9hbGlhc3EnaAZYBwAA"
        "AHNlZ21lbnRxKGgQWA0AAABzZWdtZW50X2FsaWFzcSloEFgFAAAAZXJyb3JxKmNuYXJyYXRpdmVfbG9uZ2Zvcm1f"
        "c2VnbWVudApTZWdtZW50YXRpb25JbmZlYXNpYmxlCnErKFgdAAAAZW1wdHkgb3Igd2hpdGVzcGFjZS1vbmx5IHRl"
        "eHRxLHRxLVJxLn1xL2ghaABzYlgLAAAAZXJyb3JfYWxpYXNxMGguWAcAAABjbGFzc2VzcTEoaA5oA2grdHEyWAoA"
        "AABwcm9qZWN0aW9ucTN9cTQoaAhoCWgKaAtoDE2IE2gjaCRoJWgmWAoAAABuX3NlZ21lbnRzcTVLAmgNXXE2KH1x"
        "NyhoEksAaBVNugtoFmgXdX1xOChoEksBaBVNIAxoFmgbdWVoHV1xOSh9cTooaB9oIGgVSwB1fXE7KGgfaCBoFUsA"
        "dWV1WAQAAABzZWxmcTxoAHUu"
    ),
    2: (
        "gAJ9cQAoWAYAAAByZWNvcmRxAWNuYXJyYXRpdmVfbG9uZ2Zvcm1fc2VnbWVudApTZWdtZW50YXRpb24KcQIpgXED"
        "fXEEKFgRAAAAc2VnbWVudGVyX3ZlcnNpb25xBVgeAAAAbmFycmF0aXZlLWxvbmdmb3JtLXNlZ21lbnRlci8xcQZY"
        "BAAAAHRpZXJxB1gPAAAAY2hhcHRlcl9oZWFkaW5ncQhYFAAAAHNlZ21lbnRfdGFyZ2V0X3dvcmRzcQlNiBNYCAAA"
        "AHNlZ21lbnRzcQpjbmFycmF0aXZlX2xvbmdmb3JtX3NlZ21lbnQKU2VnbWVudApxCymBcQx9cQ0oWAUAAABpbmRl"
        "eHEOSwBYBQAAAHN0YXJ0cQ9LAFgDAAAAZW5kcRBNXkZYBwAAAG5fd29yZHNxEU26C1gOAAAAY29udGVudF9zaGEy"
        "NTZxElhHAAAAc2hhMjU2OjE2MWM4NGJhMDQ1ODQzYTQzNDBjNGZjMWUyZWEzYTI0ZjJkNWVlNDVhZTQyZTM0MTZh"
        "NmZjNTQwMjk5ZmYyODZxE3ViaAspgXEUfXEVKGgOSwFoD01eRmgQTSKPaBFNIAxoElhHAAAAc2hhMjU2OjUzYjE3"
        "NDJhOGYyN2M0NDBhYzU3NmVlYzJhYzI1MzE0NDI1YmU0MmExZTlmYzZkYzM5NTliNDMwZGI2NWViMWRxFnVihnEX"
        "WA4AAABleGNsdWRlZF9zcGFuc3EYfXEZKFgGAAAAcmVhc29ucRpYGQAAAHN5bnRoZXRpYyBzaGFyZWQgbWV0YWRh"
        "dGFxG2gRSwBYBQAAAGdyYXBocRxoAHVoGYZxHVgNAAAAcGFyYW1zX3NoYTI1NnEeWEcAAABzaGEyNTY6ZmI2ZmY3"
        "NTZlYmZlNGJmMDY4OTgwYmM4NjBjOTc1MTdlOGMwNWVjMWIyNWYxNTRkMzQ0ODhlMGRjNjMxYmRkY3EfWBcAAABi"
        "b3VuZGFyeV9vZmZzZXRzX3NoYTI1NnEgWEcAAABzaGEyNTY6NDllY2FmZTY2MjIxY2NkOTZlYzMxZGQ2MjJhMTgw"
        "N2Y3MzYxYmY3NDJhNDk3YmI1YWYxZGZhOTgzMjcxZjUxOXEhdWJYDAAAAHJlY29yZF9hbGlhc3EiaANYBwAAAHNl"
        "Z21lbnRxI2gMWA0AAABzZWdtZW50X2FsaWFzcSRoDFgFAAAAZXJyb3JxJWNuYXJyYXRpdmVfbG9uZ2Zvcm1fc2Vn"
        "bWVudApTZWdtZW50YXRpb25JbmZlYXNpYmxlCnEmWB0AAABlbXB0eSBvciB3aGl0ZXNwYWNlLW9ubHkgdGV4dHEn"
        "hXEoUnEpfXEqaBxoAHNiWAsAAABlcnJvcl9hbGlhc3EraClYBwAAAGNsYXNzZXNxLGgLaAJoJodxLVgKAAAAcHJv"
        "amVjdGlvbnEufXEvKGgFaAZoB2gIaAlNiBNoHmgfaCBoIVgKAAAAbl9zZWdtZW50c3EwSwJoCl1xMSh9cTIoaA5L"
        "AGgRTboLaBJoE3V9cTMoaA5LAWgRTSAMaBJoFnVlaBhdcTQofXE1KGgaaBtoEUsAdX1xNihoGmgbaBFLAHVldVgE"
        "AAAAc2VsZnE3aAB1Lg=="
    ),
    3: (
        "gAN9cQAoWAYAAAByZWNvcmRxAWNuYXJyYXRpdmVfbG9uZ2Zvcm1fc2VnbWVudApTZWdtZW50YXRpb24KcQIpgXED"
        "fXEEKFgRAAAAc2VnbWVudGVyX3ZlcnNpb25xBVgeAAAAbmFycmF0aXZlLWxvbmdmb3JtLXNlZ21lbnRlci8xcQZY"
        "BAAAAHRpZXJxB1gPAAAAY2hhcHRlcl9oZWFkaW5ncQhYFAAAAHNlZ21lbnRfdGFyZ2V0X3dvcmRzcQlNiBNYCAAA"
        "AHNlZ21lbnRzcQpjbmFycmF0aXZlX2xvbmdmb3JtX3NlZ21lbnQKU2VnbWVudApxCymBcQx9cQ0oWAUAAABpbmRl"
        "eHEOSwBYBQAAAHN0YXJ0cQ9LAFgDAAAAZW5kcRBNXkZYBwAAAG5fd29yZHNxEU26C1gOAAAAY29udGVudF9zaGEy"
        "NTZxElhHAAAAc2hhMjU2OjE2MWM4NGJhMDQ1ODQzYTQzNDBjNGZjMWUyZWEzYTI0ZjJkNWVlNDVhZTQyZTM0MTZh"
        "NmZjNTQwMjk5ZmYyODZxE3ViaAspgXEUfXEVKGgOSwFoD01eRmgQTSKPaBFNIAxoElhHAAAAc2hhMjU2OjUzYjE3"
        "NDJhOGYyN2M0NDBhYzU3NmVlYzJhYzI1MzE0NDI1YmU0MmExZTlmYzZkYzM5NTliNDMwZGI2NWViMWRxFnVihnEX"
        "WA4AAABleGNsdWRlZF9zcGFuc3EYfXEZKFgGAAAAcmVhc29ucRpYGQAAAHN5bnRoZXRpYyBzaGFyZWQgbWV0YWRh"
        "dGFxG2gRSwBYBQAAAGdyYXBocRxoAHVoGYZxHVgNAAAAcGFyYW1zX3NoYTI1NnEeWEcAAABzaGEyNTY6ZmI2ZmY3"
        "NTZlYmZlNGJmMDY4OTgwYmM4NjBjOTc1MTdlOGMwNWVjMWIyNWYxNTRkMzQ0ODhlMGRjNjMxYmRkY3EfWBcAAABi"
        "b3VuZGFyeV9vZmZzZXRzX3NoYTI1NnEgWEcAAABzaGEyNTY6NDllY2FmZTY2MjIxY2NkOTZlYzMxZGQ2MjJhMTgw"
        "N2Y3MzYxYmY3NDJhNDk3YmI1YWYxZGZhOTgzMjcxZjUxOXEhdWJYDAAAAHJlY29yZF9hbGlhc3EiaANYBwAAAHNl"
        "Z21lbnRxI2gMWA0AAABzZWdtZW50X2FsaWFzcSRoDFgFAAAAZXJyb3JxJWNuYXJyYXRpdmVfbG9uZ2Zvcm1fc2Vn"
        "bWVudApTZWdtZW50YXRpb25JbmZlYXNpYmxlCnEmWB0AAABlbXB0eSBvciB3aGl0ZXNwYWNlLW9ubHkgdGV4dHEn"
        "hXEoUnEpfXEqaBxoAHNiWAsAAABlcnJvcl9hbGlhc3EraClYBwAAAGNsYXNzZXNxLGgLaAJoJodxLVgKAAAAcHJv"
        "amVjdGlvbnEufXEvKGgFaAZoB2gIaAlNiBNoHmgfaCBoIVgKAAAAbl9zZWdtZW50c3EwSwJoCl1xMSh9cTIoaA5L"
        "AGgRTboLaBJoE3V9cTMoaA5LAWgRTSAMaBJoFnVlaBhdcTQofXE1KGgaaBtoEUsAdX1xNihoGmgbaBFLAHVldVgE"
        "AAAAc2VsZnE3aAB1Lg=="
    ),
    4: (
        "gASV7QMAAAAAAAB9lCiMBnJlY29yZJSMGm5hcnJhdGl2ZV9sb25nZm9ybV9zZWdtZW50lIwMU2VnbWVudGF0aW9u"
        "lJOUKYGUfZQojBFzZWdtZW50ZXJfdmVyc2lvbpSMHm5hcnJhdGl2ZS1sb25nZm9ybS1zZWdtZW50ZXIvMZSMBHRp"
        "ZXKUjA9jaGFwdGVyX2hlYWRpbmeUjBRzZWdtZW50X3RhcmdldF93b3Jkc5RNiBOMCHNlZ21lbnRzlGgCjAdTZWdt"
        "ZW50lJOUKYGUfZQojAVpbmRleJRLAIwFc3RhcnSUSwCMA2VuZJRNXkaMB25fd29yZHOUTboLjA5jb250ZW50X3No"
        "YTI1NpSMR3NoYTI1NjoxNjFjODRiYTA0NTg0M2E0MzQwYzRmYzFlMmVhM2EyNGYyZDVlZTQ1YWU0MmUzNDE2YTZm"
        "YzU0MDI5OWZmMjg2lHViaA4pgZR9lChoEUsBaBJNXkZoE00ij2gUTSAMaBWMR3NoYTI1Njo1M2IxNzQyYThmMjdj"
        "NDQwYWM1NzZlZWMyYWMyNTMxNDQyNWJlNDJhMWU5ZmM2ZGMzOTU5YjQzMGRiNjVlYjFklHVihpSMDmV4Y2x1ZGVk"
        "X3NwYW5zlH2UKIwGcmVhc29ulIwZc3ludGhldGljIHNoYXJlZCBtZXRhZGF0YZRoFEsAjAVncmFwaJRoAHVoHIaU"
        "jA1wYXJhbXNfc2hhMjU2lIxHc2hhMjU2OmZiNmZmNzU2ZWJmZTRiZjA2ODk4MGJjODYwYzk3NTE3ZThjMDVlYzFi"
        "MjVmMTU0ZDM0NDg4ZTBkYzYzMWJkZGOUjBdib3VuZGFyeV9vZmZzZXRzX3NoYTI1NpSMR3NoYTI1Njo0OWVjYWZl"
        "NjYyMjFjY2Q5NmVjMzFkZDYyMmExODA3ZjczNjFiZjc0MmE0OTdiYjVhZjFkZmE5ODMyNzFmNTE5lHVijAxyZWNv"
        "cmRfYWxpYXOUaAWMB3NlZ21lbnSUaA+MDXNlZ21lbnRfYWxpYXOUaA+MBWVycm9ylGgCjBZTZWdtZW50YXRpb25J"
        "bmZlYXNpYmxllJOUjB1lbXB0eSBvciB3aGl0ZXNwYWNlLW9ubHkgdGV4dJSFlFKUfZRoH2gAc2KMC2Vycm9yX2Fs"
        "aWFzlGgtjAdjbGFzc2VzlGgOaARoKoeUjApwcm9qZWN0aW9ulH2UKGgHaAhoCWgKaAtNiBNoIWgiaCNoJIwKbl9z"
        "ZWdtZW50c5RLAmgMXZQofZQoaBFLAGgUTboLaBVoFnV9lChoEUsBaBRNIAxoFWgZdWVoG12UKH2UKGgdaB5oFEsA"
        "dX2UKGgdaB5oFEsAdWV1jARzZWxmlGgAdS4="
    ),
    5: (
        "gAWV7QMAAAAAAAB9lCiMBnJlY29yZJSMGm5hcnJhdGl2ZV9sb25nZm9ybV9zZWdtZW50lIwMU2VnbWVudGF0aW9u"
        "lJOUKYGUfZQojBFzZWdtZW50ZXJfdmVyc2lvbpSMHm5hcnJhdGl2ZS1sb25nZm9ybS1zZWdtZW50ZXIvMZSMBHRp"
        "ZXKUjA9jaGFwdGVyX2hlYWRpbmeUjBRzZWdtZW50X3RhcmdldF93b3Jkc5RNiBOMCHNlZ21lbnRzlGgCjAdTZWdt"
        "ZW50lJOUKYGUfZQojAVpbmRleJRLAIwFc3RhcnSUSwCMA2VuZJRNXkaMB25fd29yZHOUTboLjA5jb250ZW50X3No"
        "YTI1NpSMR3NoYTI1NjoxNjFjODRiYTA0NTg0M2E0MzQwYzRmYzFlMmVhM2EyNGYyZDVlZTQ1YWU0MmUzNDE2YTZm"
        "YzU0MDI5OWZmMjg2lHViaA4pgZR9lChoEUsBaBJNXkZoE00ij2gUTSAMaBWMR3NoYTI1Njo1M2IxNzQyYThmMjdj"
        "NDQwYWM1NzZlZWMyYWMyNTMxNDQyNWJlNDJhMWU5ZmM2ZGMzOTU5YjQzMGRiNjVlYjFklHVihpSMDmV4Y2x1ZGVk"
        "X3NwYW5zlH2UKIwGcmVhc29ulIwZc3ludGhldGljIHNoYXJlZCBtZXRhZGF0YZRoFEsAjAVncmFwaJRoAHVoHIaU"
        "jA1wYXJhbXNfc2hhMjU2lIxHc2hhMjU2OmZiNmZmNzU2ZWJmZTRiZjA2ODk4MGJjODYwYzk3NTE3ZThjMDVlYzFi"
        "MjVmMTU0ZDM0NDg4ZTBkYzYzMWJkZGOUjBdib3VuZGFyeV9vZmZzZXRzX3NoYTI1NpSMR3NoYTI1Njo0OWVjYWZl"
        "NjYyMjFjY2Q5NmVjMzFkZDYyMmExODA3ZjczNjFiZjc0MmE0OTdiYjVhZjFkZmE5ODMyNzFmNTE5lHVijAxyZWNv"
        "cmRfYWxpYXOUaAWMB3NlZ21lbnSUaA+MDXNlZ21lbnRfYWxpYXOUaA+MBWVycm9ylGgCjBZTZWdtZW50YXRpb25J"
        "bmZlYXNpYmxllJOUjB1lbXB0eSBvciB3aGl0ZXNwYWNlLW9ubHkgdGV4dJSFlFKUfZRoH2gAc2KMC2Vycm9yX2Fs"
        "aWFzlGgtjAdjbGFzc2VzlGgOaARoKoeUjApwcm9qZWN0aW9ulH2UKGgHaAhoCWgKaAtNiBNoIWgiaCNoJIwKbl9z"
        "ZWdtZW50c5RLAmgMXZQofZQoaBFLAGgUTboLaBVoFnV9lChoEUsBaBRNIAxoFWgZdWVoG12UKH2UKGgdaB5oFEsA"
        "dX2UKGgdaB5oFEsAdWV1jARzZWxmlGgAdS4="
    ),
}



@pytest.fixture(scope="module")
def copied_plugin(tmp_path_factory):
    sandbox = tmp_path_factory.mktemp("p3-longform")
    plugin = sandbox / "copied-plugin"
    shutil.copytree(
        PLUGIN_ROOT, plugin,
        ignore=shutil.ignore_patterns("__pycache__", "tests"),
    )
    (sandbox / "foreign-cwd").mkdir()
    return plugin


def _run(plugin, code=None, *, script=None, stdin=None):
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    env.pop("PYTHONHOME", None)
    env["PYTHONUTF8"] = "1"
    # -I removes cwd/user-site/env import paths; -S excludes installed backends.
    command = [sys.executable, "-I", "-S", "-B"]
    command += [str(script)] if script is not None else ["-c", textwrap.dedent(code)]
    return subprocess.run(
        command, cwd=plugin.parent / "foreign-cwd", env=env,
        input=stdin, capture_output=True, text=True, encoding="utf-8", timeout=30,
    )


def _success(result, *, silent=True):
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stderr == ""
    if silent:
        assert result.stdout == ""


@pytest.mark.parametrize("name", MODULES)
@pytest.mark.parametrize("package_first", [False, True], ids=["legacy-first", "package-first"])
def test_import_identity_and_bidirectional_monkeypatch(copied_plugin, name, package_first):
    code = f"""
        import importlib, sys
        sys.path.insert(0, {str(copied_plugin / 'scripts')!r})
        names = [{name!r}, {'setec.core.' + name!r}]
        if {package_first!r}:
            names.reverse()
        first, second = [importlib.import_module(n) for n in names]
        assert first is second
        legacy = importlib.import_module({name!r})
        package = importlib.import_module({'setec.core.' + name!r})
        if {name!r} == 'narrative_longform_segment':
            assert legacy.Segment is package.Segment
            assert legacy.Segmentation is package.Segmentation
            assert legacy.SegmentationInfeasible is package.SegmentationInfeasible
            original = legacy.count_words
            legacy.count_words = lambda text: 41
            assert package.count_words('synthetic input') == 41
            package.count_words = lambda text: 73
            assert legacy.count_words('synthetic input') == 73
            package.count_words = original
            assert legacy.count_words('two words') == 2
        else:
            original = legacy.framed_digest
            legacy.framed_digest = lambda domain, payload: ('legacy', domain, payload)
            assert package.source_work_sha256('sample') == (
                'legacy', 'setec.voiceprint.spec78.source-work-content.v1', b'sample')
            package.framed_digest = lambda domain, payload: ('package', domain, payload)
            assert legacy.framed_json_digest(
                'setec.voiceprint.spec78.derivation-json.v1', {{'b': 2, 'a': 1}}) == (
                'package', 'setec.voiceprint.spec78.derivation-json.v1', b'{{"a":1,"b":2}}')
            legacy.framed_digest = original
            assert package.source_work_sha256('sample') == legacy.source_work_sha256('sample')
    """
    _success(_run(copied_plugin, code))


@pytest.mark.parametrize("polarity_first", [False, True], ids=["segment-first", "polarity-first"])
@pytest.mark.parametrize("package_first", [False, True], ids=["legacy-first", "package-first"])
def test_cross_module_word_count_delegation(copied_plugin, polarity_first, package_first):
    code = f"""
        import importlib, sys
        sys.path.insert(0, {str(copied_plugin / 'scripts')!r})
        names = {list(MODULES)!r}
        if {polarity_first!r}:
            names.reverse()
        prefix = 'setec.core.' if {package_first!r} else ''
        for name in names:
            importlib.import_module(prefix + name)
        legacy = importlib.import_module('narrative_longform_segment')
        package = importlib.import_module('setec.core.narrative_longform_segment')
        old_polarity = importlib.import_module('storyscope_polarity_contract')
        new_polarity = importlib.import_module('setec.core.storyscope_polarity_contract')
        assert old_polarity is new_polarity
        assert old_polarity.nls is new_polarity.nls is legacy is package
        calls = []
        def from_legacy(text):
            calls.append(('legacy', text))
            return 41
        legacy.count_words = from_legacy
        assert new_polarity.count_source_words('first sample') == 41
        def from_package(text):
            calls.append(('package', text))
            return 73
        package.count_words = from_package
        assert old_polarity.count_source_words('second sample') == 73
        assert calls == [('legacy', 'first sample'), ('package', 'second sample')]
    """
    _success(_run(copied_plugin, code))


def test_package_only_nested_type_hints(copied_plugin):
    code = f"""
        import sys, typing
        sys.path.insert(0, {str(copied_plugin / 'scripts')!r})
        from setec.core import narrative_longform_segment as nls
        from setec.core import storyscope_polarity_contract as polarity
        # No legacy import and no manually supplied annotation namespaces.
        assert typing.get_type_hints(nls.Segment)['start'] is int
        assert typing.get_type_hints(nls.Segment)['content_sha256'] is str
        assert typing.get_type_hints(nls.Segment.__init__)['end'] is int
        assert typing.get_type_hints(nls.Segment.text) == {{'source': str, 'return': str}}
        assert typing.get_type_hints(nls.Segmentation)['segments'] == tuple[nls.Segment, ...]
        assert typing.get_type_hints(nls.Segmentation)['excluded_spans'] == tuple[dict, ...]
        assert typing.get_type_hints(nls.Segmentation.__init__)['segments'] == tuple[nls.Segment, ...]
        assert typing.get_type_hints(nls.Segmentation.n_segments.fget)['return'] is int
        assert typing.get_type_hints(nls.Segmentation.segment_words.fget)['return'] == list[int]
        assert typing.get_type_hints(nls.segment_text)['return'] is nls.Segmentation
        assert typing.get_type_hints(nls.segmentation_dict)['seg'] is nls.Segmentation
        assert typing.get_type_hints(polarity.count_source_words) == {{'text': str, 'return': int}}
        assert typing.get_type_hints(polarity.framed_file_digest)['path'] is __import__('pathlib').Path
        record = nls.segment_text('synthetic two words')
        assert record.n_segments == 1 and record.segment_words == [3]
        assert record.segments[0].text('synthetic two words') == 'synthetic two words'
    """
    _success(_run(copied_plugin, code))


# Both producer and consumer run this same behavioral exercise. Neither changes
# class module names, substitutes pickle globals, nor installs custom reducers.
_PICKLE_EXERCISE = """
import base64, dataclasses, importlib, pickle, sys
if not writing:
    # Cold readers must resolve legacy globals themselves, before any explicit
    # segmenter import can pre-register an alias and conceal a broken launcher.
    graph = pickle.loads(base64.b64decode(sys.stdin.read()))
nls = importlib.import_module(module_name)
source = ''.join('CHAPTER %d.\\r\\n%s\\r\\n' % (i, 'token ' * size)
                 for i, size in enumerate((3000, 3000, 100), 1))

def make_graph():
    record = nls.segment_text(source)
    metadata = {'reason': 'synthetic shared metadata', 'n_words': 0}
    record = dataclasses.replace(record, excluded_spans=(metadata, metadata))
    try:
        nls.segment_text(' \\t\\r\\n')
    except nls.SegmentationInfeasible as exc:
        error = exc
    else:
        raise AssertionError('empty input did not refuse')
    graph = {'record': record, 'record_alias': record,
             'segment': record.segments[0], 'segment_alias': record.segments[0],
             'error': error, 'error_alias': error,
             'classes': (nls.Segment, nls.Segmentation, nls.SegmentationInfeasible),
             'projection': nls.segmentation_dict(record)}
    graph['self'] = graph
    metadata['graph'] = graph
    error.graph = graph
    return graph

def check_graph(graph):
    record = graph['record']
    assert type(record) is nls.Segmentation
    assert graph['record_alias'] is record
    assert type(graph['segment']) is nls.Segment
    assert graph['segment_alias'] is graph['segment'] is record.segments[0]
    assert graph['classes'] == (nls.Segment, nls.Segmentation, nls.SegmentationInfeasible)
    # These globals are the externally consumed pickle compatibility contract.
    assert all(cls.__module__ == 'narrative_longform_segment' for cls in graph['classes'])
    assert graph['self'] is graph
    assert record.excluded_spans[0] is record.excluded_spans[1]
    assert record.excluded_spans[0]['graph'] is graph
    assert type(graph['error']) is nls.SegmentationInfeasible
    assert graph['error_alias'] is graph['error']
    assert graph['error'].graph is graph
    assert graph['error'].args == ('empty or whitespace-only text',)
    try:
        raise graph['error']
    except nls.SegmentationInfeasible as caught:
        assert caught is graph['error']
    assert record.n_segments == 2
    assert record.segment_words == [3002, 3104]
    assert ''.join(segment.text(source) for segment in record.segments) == source
    expected = nls.segment_text(source)
    assert record.segments == expected.segments
    assert record.tier == expected.tier == 'chapter_heading'
    assert record.params_sha256 == expected.params_sha256
    assert record.boundary_offsets_sha256 == expected.boundary_offsets_sha256
    assert graph['projection']['segments'] == nls.segmentation_dict(expected)['segments']
    assert graph['projection']['excluded_spans'][0]['reason'] == 'synthetic shared metadata'
    try:
        record.tier = 'changed'
    except dataclasses.FrozenInstanceError:
        pass
    else:
        raise AssertionError('record lost frozen behavior')

if writing:
    graph = make_graph()
check_graph(graph)
payload = pickle.dumps(graph, protocol=protocol)
check_graph(pickle.loads(payload))
if writing:
    print(base64.b64encode(payload).decode('ascii'))
"""


def _pickle_code(plugin, name, protocol, *, writing):
    return (
        f"import sys\nsys.path.insert(0, {str(plugin / 'scripts')!r})\n"
        f"module_name = {name!r}\nprotocol = {protocol}\nwriting = {writing!r}\n"
        + _PICKLE_EXERCISE
    )


@pytest.mark.parametrize("protocol", range(6))
def test_baseline_pickle_graph_loads_cold(copied_plugin, protocol):
    code = _pickle_code(copied_plugin, "setec.core.narrative_longform_segment", protocol, writing=False)
    _success(_run(copied_plugin, code, stdin=_BASELINE_PICKLES[protocol]))


@pytest.mark.parametrize("protocol", range(6))
def test_package_pickle_graph_loads_through_cold_legacy_import(copied_plugin, protocol):
    producer = _pickle_code(copied_plugin, "setec.core.narrative_longform_segment", protocol, writing=True)
    consumer = _pickle_code(copied_plugin, "narrative_longform_segment", protocol, writing=False)
    generated = _run(copied_plugin, producer)
    _success(generated, silent=False)
    _success(_run(copied_plugin, consumer, stdin=generated.stdout))


@pytest.mark.parametrize("name", MODULES)
def test_direct_execution_is_silent(copied_plugin, name):
    _success(_run(copied_plugin, script=copied_plugin / "scripts" / f"{name}.py"))


@pytest.mark.parametrize("name", MODULES)
@pytest.mark.parametrize("warm", [False, True], ids=["cold", "package-preloaded"])
def test_detached_runpy_preserves_main_and_import_identity(copied_plugin, name, warm):
    code = f"""
        import importlib, runpy, sys
        from pathlib import Path
        scripts = Path({str(copied_plugin / 'scripts')!r})
        main = sys.modules['__main__']
        marker = object()
        main.p3_synthetic_marker = marker
        if {warm!r}:
            sys.path.insert(0, str(scripts))
            previous = importlib.import_module({'setec.core.' + name!r})
            sys.path.remove(str(scripts))
        assert str(scripts) not in sys.path
        runpy.run_path(str(scripts / {name + '.py'!r}), run_name='__main__')
        assert sys.modules['__main__'] is main
        assert main.p3_synthetic_marker is marker
        legacy = importlib.import_module({name!r})
        package = importlib.import_module({'setec.core.' + name!r})
        assert legacy is package
        if {warm!r}:
            assert package is previous
        runpy.run_path(str(scripts / {name + '.py'!r}), run_name='__main__')
        assert sys.modules['__main__'] is main
        assert importlib.import_module({name!r}) is package
    """
    _success(_run(copied_plugin, code))


@pytest.mark.parametrize("name", MODULES)
@pytest.mark.parametrize("package_first", [False, True], ids=["legacy-entry", "package-entry"])
def test_cold_import_failure_can_recover(copied_plugin, name, package_first):
    code = f"""
        import builtins, importlib, sys
        sys.path.insert(0, {str(copied_plugin / 'scripts')!r})
        original_import = builtins.__import__
        attempts = []
        def fail_dependency(name, *args, **kwargs):
            if name == 'dataclasses':
                attempts.append(name)
                raise ImportError('synthetic transient dependency failure')
            return original_import(name, *args, **kwargs)
        builtins.__import__ = fail_dependency
        entry = {('setec.core.' if package_first else '') + name!r}
        try:
            try:
                importlib.import_module(entry)
            except ImportError as exc:
                assert str(exc) == 'synthetic transient dependency failure'
            else:
                raise AssertionError('injected package failure was not exercised')
        finally:
            builtins.__import__ = original_import
        assert attempts
        assert 'narrative_longform_segment' not in sys.modules
        assert 'setec.core.narrative_longform_segment' not in sys.modules
        recovered = importlib.import_module(entry)
        assert recovered is importlib.import_module({name!r})
        assert recovered is importlib.import_module({'setec.core.' + name!r})
        from setec.core import narrative_longform_segment as nls
        import narrative_longform_segment as legacy
        from setec.core import storyscope_polarity_contract as polarity
        assert legacy is nls is polarity.nls
        assert nls.segment_text('recovered sample').segment_words == [2]
        assert polarity.count_source_words('recovered sample') == 2
    """
    _success(_run(copied_plugin, code))
