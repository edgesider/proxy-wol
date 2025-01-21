import typing

from aiohttp import web
from aiohttp.web import Request, RouteTableDef
from dbus_next.aio import MessageBus

routers = RouteTableDef()
dbus: MessageBus | None = None


@routers.get('/touch')
async def get(_req: Request):
    global dbus
    if dbus is None:
        dbus = await MessageBus().connect()
    intro = await dbus.introspect('org.freedesktop.ScreenSaver', '/ScreenSaver')
    obj = dbus.get_proxy_object('org.freedesktop.ScreenSaver', '/ScreenSaver', intro)
    iface: typing.Any = obj.get_interface('org.freedesktop.ScreenSaver')
    await iface.call_simulate_user_activity()
    return web.Response(status=200, body='success\n')


@routers.get('/ping')
async def ping(_req):
    return web.Response(status=200, body='pong\n')


app = web.Application()
app.add_routes(routers)


async def get_app():
    return app


if __name__ == '__main__':
    web.run_app(app, port=4322)
