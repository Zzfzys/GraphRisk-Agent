"""Small bounded HTTP/strict JSON primitives, without importing old ML workflows."""
import asyncio
import json
import re
import httpx


class TransportFailure(ValueError):
    def __init__(self,code,*,http_status=None,request_id=None):
        super().__init__(code)
        self.http_status=http_status
        self.request_id=request_id if isinstance(request_id,str) and re.fullmatch(r'[A-Za-z0-9_.:-]{1,128}',request_id) else None


def strict_json(text):
    def pairs(items):
        output={}
        for key,value in items:
            if key in output:raise ValueError("duplicate_json_key")
            output[key]=value
        return output
    def invalid(_):raise ValueError("nonfinite_json")
    return json.loads(text,object_pairs_hook=pairs,parse_constant=invalid)


def post_one(url, payload, *, headers, timeout, transport=None, proxy=None):
    """One POST, no automatic retries, redirects, proxy inheritance or streaming API."""
    if proxy not in (None, 'http://127.0.0.1:7890'):
        raise TransportFailure('unreviewed_proxy_route')
    async def request():
        async with httpx.AsyncClient(transport=transport,proxy=proxy,verify=True,trust_env=False,follow_redirects=False,timeout=timeout) as client:
            async with client.stream("POST",url,json=payload,headers=headers) as response:
                if response.status_code!=200:
                    raise TransportFailure("authentication_failed" if response.status_code in (401,403) else
                                           "rate_limited" if response.status_code==429 else "provider_http_rejected",http_status=response.status_code,request_id=response.headers.get("x-request-id"))
                raw=bytearray()
                async for chunk in response.aiter_bytes():
                    raw.extend(chunk)
                    if len(raw)>131072:raise TransportFailure("provider_response_too_large")
                value=strict_json(raw.decode("utf-8"))
                if not isinstance(value,dict):raise ValueError
                return value
    async def bounded():return await asyncio.wait_for(request(),timeout=timeout)
    try:return asyncio.run(bounded())
    except (httpx.TimeoutException,asyncio.TimeoutError):raise TransportFailure("provider_timeout_unknown") from None
    except httpx.HTTPError:raise TransportFailure("provider_connection_unknown") from None
    except (ValueError,UnicodeError) as error:
        if isinstance(error,TransportFailure):raise
        raise TransportFailure("invalid_provider_response_unknown") from None
