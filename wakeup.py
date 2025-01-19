"""
- agent mode
    - /agent/config (set ip & mac)
    - /agent/wakeup
    - /agent/touch
- machine mode
    - /machine/touch (dbus SimulateUserActivity)
"""
import typing

from aiohttp import web
from aiohttp.web import Request, RouteTableDef
from dbus_next.aio import MessageBus

routers = RouteTableDef()
dbus: MessageBus | None = None


@routers.get('/machine/touch')
async def get(_req: Request):
    global dbus
    if dbus is None:
        dbus = await MessageBus().connect()
    intro = await dbus.introspect('org.freedesktop.ScreenSaver', '/ScreenSaver')
    obj = dbus.get_proxy_object('org.freedesktop.ScreenSaver', '/ScreenSaver', intro)
    iface: typing.Any = obj.get_interface('org.freedesktop.ScreenSaver')
    await iface.SimulateUserActivity()


if __name__ == '__main__':
    app = web.Application()
    app.add_routes(routers)
    web.run_app(app)
