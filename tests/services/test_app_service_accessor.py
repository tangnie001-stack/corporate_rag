"""AppService 单例访问器下沉到 services 后，api 层只做再导出。"""


def test_api_dependencies_reexports_services_accessor():
    from src.api import dependencies as dep_mod
    from src.services import app_service as svc_mod

    assert dep_mod.get_app_service is svc_mod.get_app_service


def test_accessor_is_a_coroutine_function():
    import inspect

    from src.services.app_service import get_app_service

    assert inspect.iscoroutinefunction(get_app_service)
