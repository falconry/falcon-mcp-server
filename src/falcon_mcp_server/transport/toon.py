"""Token-Oriented Object Notation (TOON) de/serialization."""

import falcon.media
import falcon.typing
import toon_format


class TOONHandler(falcon.media.BaseHandler):
    """Token-Oriented Object Notation (TOON) media handler for Falcon."""

    def serialize(self, media: object, content_type: str) -> bytes:
        return toon_format.encode(media).encode()

    def deserialize(
        self,
        stream: falcon.typing.ReadableIO,
        content_type: str | None,
        content_length: int | None,
    ) -> object:
        data = stream.read()
        return toon_format.decode(data.decode())
