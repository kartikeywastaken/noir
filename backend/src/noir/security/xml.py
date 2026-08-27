"""XML parsing with DTDs, external references and entities prohibited."""

import xml.etree.ElementTree as ET

from defusedxml import ElementTree as SafeET
from defusedxml.common import DefusedXmlException


def parse(path):
    try:
        return SafeET.parse(path, forbid_dtd=True, forbid_entities=True, forbid_external=True)
    except DefusedXmlException as exc:
        raise ET.ParseError("Unsafe XML declaration rejected") from exc


def fromstring(text):
    try:
        return SafeET.fromstring(text, forbid_dtd=True, forbid_entities=True, forbid_external=True)
    except DefusedXmlException as exc:
        raise ET.ParseError("Unsafe XML declaration rejected") from exc
