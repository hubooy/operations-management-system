from django.views.decorators.http import require_http_methods

from .errors import WorkflowApiError
from .import_chain_status import read_today_status
from .revisions import revision_value
from .views import _error, _json, _principal


@require_http_methods(["GET"])
def today_status(request):
    try:
        _principal(request, {"viewer", "analyst", "operator", "admin"})
        if request.GET:
            raise WorkflowApiError("此接口仅查询服务器当前上海日期，不接受筛选参数。")
        payload = read_today_status()
        # Workflow revision authenticates the existing gateway contract only;
        # checkedAt identifies this independent n8n read snapshot. No caching.
        return _json(payload, revision=revision_value())
    except Exception as error:
        return _error(error, "读取 n8n 今日状态失败。")
