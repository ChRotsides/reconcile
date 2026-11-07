"""Derive the local files `test_public_format_samples.py` reads, from two
downloads. Not imported by the package or the tests; run by hand:

    SAMPLES=data/public-samples
    # Xero-OpenAPI at df43c6dd06c0eccffdf09297dcae3b0dbcf3a498 (MIT):
    #   https://raw.githubusercontent.com/XeroAPI/Xero-OpenAPI/df43c6dd06c0eccffdf09297dcae3b0dbcf3a498/xero_accounting.yaml
    # ING CAMT sample PDF (licence not stated: keep it local, never commit):
    #   https://assets.ing.com/m/19ba05999348c5c7/original/Sample-Files-CAMT.pdf
    #   saved as ing-sample-camt.pdf
    uv run --with pyyaml python tests/integration/extract_public_samples.py $SAMPLES

Needs `pdftotext` (poppler) on the PATH and PyYAML (via `--with pyyaml`). The
directory must hold `xero_accounting.yaml` and `ing-sample-camt.pdf`. Writes
`xero_examples/*.json`, `ing.txt`, `ing_camt053_sample.xml` and
`ing_ntry_snippets.xml` into it. Everything written is derived from a download
and stays out of git (`data/` is ignored).
"""

import json
import re
import subprocess
import sys
from pathlib import Path

import yaml  # type: ignore[import-untyped]  # supplied by uv --with

# Element names the PDF text shows in lower case; the seven example entries are
# restored to the usual camel case from this list, so that file is a
# reconstruction, not the bank's file.
ELEMENT_NAMES = (
    "Ntry NtryRef Amt Ccy CdtDbtInd Sts BookgDt Dt ValDt AcctSvcrRef BkTxCd Domn "
    "Cd Fmly SubFmlyCd Prtry Issr NtryDtls TxDtls AmtDtls TxAmt RltdPties Dbtr Nm "
    "DbtrAcct Id IBAN Tp RltdAgts DbtrAgt CdtrAgt FinInstnId BIC RmtInf Ustrd "
    "RltdDts TxDtTm Refs EndToEndId Cdtr CdtrAcct SchmeNm Pty PrvtId Othr MndtId "
    "AddtlTxInf Strd CdtrRefInf Ref CdOrPrtry AddtlNtryInf"
).split()
CANON = {n.lower(): n for n in ELEMENT_NAMES}

# Wrapper for the loose entries. It states no account currency, because the
# examples mix currencies, which no single account statement does.
WRAPPER = """<?xml version="1.0" encoding="utf-8"?>
<Document xmlns="urn:iso:std:iso:20022:tech:xsd:camt.053.001.02"><BkToCstmrStmt>
<GrpHdr><MsgId>M1</MsgId><CreDtTm>2025-06-01T00:00:00</CreDtTm></GrpHdr>
<Stmt><Id>SNIPPETS</Id><FrToDt><FrDtTm>2025-01-01T00:00:00</FrDtTm><ToDtTm>2025-12-31T23:59:59</ToDtTm></FrToDt>
<Acct><Id><IBAN>NL08INGB0000001234</IBAN></Id></Acct>
{entries}
</Stmt></BkToCstmrStmt></Document>
"""


class _TextDates(yaml.SafeLoader):  # type: ignore[misc]
    """SafeLoader that leaves dates as text."""


_TextDates.yaml_implicit_resolvers = {
    k: [(tag, rx) for tag, rx in v if tag != "tag:yaml.org,2002:timestamp"]
    for k, v in yaml.SafeLoader.yaml_implicit_resolvers.items()
}


def xero_examples(root: Path) -> None:
    spec = yaml.load((root / "xero_accounting.yaml").read_text(), Loader=_TextDates)
    out = root / "xero_examples"
    out.mkdir(exist_ok=True)
    for endpoint in ("Invoices", "Payments", "BatchPayments", "Journals"):
        response = spec["paths"]["/" + endpoint]["get"]["responses"]["200"]
        example = response["content"]["application/json"]["example"]
        (out / f"{endpoint}.json").write_text(json.dumps(example, indent=1))


def ing_samples(root: Path) -> None:
    text_file = root / "ing.txt"
    subprocess.run(
        ["pdftotext", "-layout", str(root / "ing-sample-camt.pdf"), str(text_file)],
        check=True,
    )
    text = text_file.read_text().replace("\x0c", "")  # page-break form feeds
    full = re.search(r"<\?xml.*?</Document>", text, re.S)
    if full is None:
        raise SystemExit("no complete <Document> in the PDF text")
    (root / "ing_camt053_sample.xml").write_text(full.group(0))

    def restore_case(m: re.Match[str]) -> str:
        return m.group(1) + CANON.get(m.group(2), m.group(2))

    entries = []
    rest = text.split("</Document>", 1)[1]
    for fragment in re.findall(r"^<ntry>.*?^</ntry>", rest, re.S | re.M):
        fixed = re.sub(r"(</?)([A-Za-z]+)", restore_case, fragment)
        entries.append(fixed.replace(" ccy=", " Ccy="))
    (root / "ing_ntry_snippets.xml").write_text(
        WRAPPER.format(entries="\n".join(entries))
    )
    print(f"{len(entries)} loose entries")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    sample_dir = Path(sys.argv[1])
    xero_examples(sample_dir)
    ing_samples(sample_dir)
