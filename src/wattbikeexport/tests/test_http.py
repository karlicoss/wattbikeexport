from wattbikeexport._http import CsrfParser


def test_csrf_parser() -> None:
    parser = CsrfParser()
    parser.feed('<input type="hidden" name="_csrf" value="token">')
    assert parser.token == "token"
