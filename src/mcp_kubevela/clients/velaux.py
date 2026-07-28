"""VelaUX REST API 逐接口封装。

契约以 velaux 源码为准（pkg/server/interfaces/api/ 及 dto/v1/types.go）：
- 前缀 /api/v1，认证 Authorization: Bearer <accessToken>
- 分页统一 page / pageSize（应用列表接口无分页）
- 注意：工作流记录的 resume / terminate / rollback 是 GET 方法
"""

from __future__ import annotations

from typing import Any, Optional

from .base import VelaClientBase


class VelaUXClient(VelaClientBase):
    """VelaUX API 客户端"""

    # ================= 应用 =================
    async def list_applications(
        self,
        query: Optional[str] = None,
        project: Optional[str] = None,
        env: Optional[str] = None,
        target_name: Optional[str] = None,
    ) -> dict[str, Any]:
        """GET /applications（无分页，按条件过滤）"""
        return await self.request(
            "GET",
            "/applications",
            params={
                "query": query,
                "project": project,
                "env": env,
                "targetName": target_name,
            },
        )

    async def get_application(self, app_name: str) -> dict[str, Any]:
        """GET /applications/{appName}"""
        return await self.request("GET", f"/applications/{app_name}")

    async def create_application(
        self,
        name: str,
        project: str,
        component: dict[str, Any],
        alias: Optional[str] = None,
        description: Optional[str] = None,
        icon: str = "",
        labels: Optional[dict[str, str]] = None,
        env_binding: Optional[list[str]] = None,
    ) -> dict[str, Any]:
        """POST /applications

        component: {"name", "componentType", "properties"(JSON 字符串), ...}
        env_binding: 环境名列表，转换为 [{"name": env}, ...]
        """
        body: dict[str, Any] = {
            "name": name,
            "project": project,
            "icon": icon,
            "component": component,
        }
        if alias:
            body["alias"] = alias
        if description:
            body["description"] = description
        if labels:
            body["labels"] = labels
        if env_binding:
            body["envBinding"] = [{"name": e} for e in env_binding]
        return await self.request("POST", "/applications", json_body=body)

    async def get_application_status(
        self, app_name: str, env: Optional[str] = None
    ) -> dict[str, Any]:
        """GET /applications/{app}/status 或 /applications/{app}/envs/{env}/status"""
        if env:
            return await self.request(
                "GET", f"/applications/{app_name}/envs/{env}/status"
            )
        return await self.request("GET", f"/applications/{app_name}/status")

    async def deploy_application(
        self,
        app_name: str,
        workflow_name: Optional[str] = None,
        note: Optional[str] = None,
        force: bool = False,
    ) -> dict[str, Any]:
        """POST /applications/{appName}/deploy（异步触发，返回 record）"""
        body: dict[str, Any] = {"triggerType": "api", "force": force}
        if workflow_name:
            body["workflowName"] = workflow_name
        if note:
            body["note"] = note
        return await self.request(
            "POST", f"/applications/{app_name}/deploy", json_body=body
        )

    async def dry_run_application(
        self,
        app_name: str,
        env: Optional[str] = None,
        workflow: Optional[str] = None,
        version: Optional[str] = None,
    ) -> dict[str, Any]:
        """POST /applications/{appName}/dry-run"""
        body: dict[str, Any] = {
            "dryRunType": "REVISION" if version else "APP",
        }
        if env:
            body["env"] = env
        if workflow:
            body["workflow"] = workflow
        if version:
            body["version"] = version
        return await self.request(
            "POST", f"/applications/{app_name}/dry-run", json_body=body
        )

    async def list_revisions(
        self,
        app_name: str,
        env: Optional[str] = None,
        status: Optional[str] = None,
        page: int = 0,
        page_size: int = 20,
    ) -> dict[str, Any]:
        """GET /applications/{appName}/revisions"""
        return await self.request(
            "GET",
            f"/applications/{app_name}/revisions",
            params={
                "envName": env,
                "status": status,
                "page": page,
                "pageSize": page_size,
            },
        )

    async def rollback_application(
        self, app_name: str, revision: str
    ) -> dict[str, Any]:
        """POST /applications/{appName}/revisions/{revision}/rollback"""
        return await self.request(
            "POST", f"/applications/{app_name}/revisions/{revision}/rollback"
        )

    async def list_components(
        self, app_name: str, env: Optional[str] = None
    ) -> dict[str, Any]:
        """GET /applications/{appName}/components"""
        return await self.request(
            "GET",
            f"/applications/{app_name}/components",
            params={"envName": env},
        )

    async def get_component(self, app_name: str, component: str) -> dict[str, Any]:
        """GET /applications/{appName}/components/{compName}"""
        return await self.request(
            "GET", f"/applications/{app_name}/components/{component}"
        )

    async def list_deploy_records(
        self, app_name: str, env: str, page: int = 0, page_size: int = 20
    ) -> dict[str, Any]:
        """GET /applications/{appName}/envs/{envName}/records"""
        return await self.request(
            "GET",
            f"/applications/{app_name}/envs/{env}/records",
            params={"page": page, "pageSize": page_size},
        )

    async def compare_application(
        self,
        app_name: str,
        env: Optional[str] = None,
        revision: Optional[str] = None,
        compare_with: str = "running",
    ) -> dict[str, Any]:
        """POST /applications/{appName}/compare

        三种模式（对应 AppCompareReq 的三个 oneof 选项）：
        - env 提供且无 revision：latest 配置 vs 集群运行态（compareLatestWithRunning，env 必填）
        - revision 提供且 compare_with="running"：指定版本 vs 集群运行态
        - revision 提供且 compare_with="latest"：指定版本 vs 最新配置

        响应含 isDiff / diffReport / baseAppYAML / targetAppYAML，
        其中 YAML 字段可用于导出应用 CR 清单。
        """
        body: dict[str, Any] = {}
        if revision:
            if compare_with == "latest":
                body["compareRevisionWithLatest"] = {"revision": revision}
            else:
                body["compareRevisionWithRunning"] = {"revision": revision}
        else:
            body["compareLatestWithRunning"] = {"env": env or ""}
        return await self.request(
            "POST", f"/applications/{app_name}/compare", json_body=body
        )

    async def list_triggers(self, app_name: str) -> dict[str, Any]:
        """GET /applications/{appName}/triggers"""
        return await self.request("GET", f"/applications/{app_name}/triggers")

    async def create_trigger(
        self,
        app_name: str,
        name: str,
        workflow_name: str,
        payload_type: str = "custom",
        alias: Optional[str] = None,
        description: Optional[str] = None,
        component_name: Optional[str] = None,
        registry: Optional[str] = None,
    ) -> dict[str, Any]:
        """POST /applications/{appName}/triggers（type 固定 webhook）"""
        body: dict[str, Any] = {
            "name": name,
            "type": "webhook",
            "workflowName": workflow_name,
            "payloadType": payload_type,
        }
        if alias:
            body["alias"] = alias
        if description:
            body["description"] = description
        if component_name:
            body["componentName"] = component_name
        if registry:
            body["registry"] = registry
        return await self.request(
            "POST", f"/applications/{app_name}/triggers", json_body=body
        )

    # ================= 工作流 =================
    async def list_workflows(self, app_name: str) -> dict[str, Any]:
        """GET /applications/{appName}/workflows"""
        return await self.request("GET", f"/applications/{app_name}/workflows")

    async def list_workflow_records(
        self,
        app_name: str,
        workflow_name: str,
        page: int = 0,
        page_size: int = 20,
    ) -> dict[str, Any]:
        """GET /applications/{app}/workflows/{wf}/records"""
        return await self.request(
            "GET",
            f"/applications/{app_name}/workflows/{workflow_name}/records",
            params={"page": page, "pageSize": page_size},
        )

    async def get_workflow_record(
        self, app_name: str, workflow_name: str, record: str
    ) -> dict[str, Any]:
        """GET /applications/{app}/workflows/{wf}/records/{record}"""
        return await self.request(
            "GET",
            f"/applications/{app_name}/workflows/{workflow_name}/records/{record}",
        )

    async def get_workflow_record_logs(
        self, app_name: str, workflow_name: str, record: str, step: str
    ) -> dict[str, Any]:
        """GET .../records/{record}/logs?step=xxx（step 必填）"""
        return await self.request(
            "GET",
            f"/applications/{app_name}/workflows/{workflow_name}/records/{record}/logs",
            params={"step": step},
        )

    async def resume_workflow_record(
        self,
        app_name: str,
        workflow_name: str,
        record: str,
        step: Optional[str] = None,
    ) -> dict[str, Any]:
        """GET .../records/{record}/resume（VelaUX 该操作为 GET 方法）"""
        return await self.request(
            "GET",
            f"/applications/{app_name}/workflows/{workflow_name}/records/{record}/resume",
            params={"step": step},
        )

    async def terminate_workflow_record(
        self, app_name: str, workflow_name: str, record: str
    ) -> dict[str, Any]:
        """GET .../records/{record}/terminate（VelaUX 该操作为 GET 方法）"""
        return await self.request(
            "GET",
            f"/applications/{app_name}/workflows/{workflow_name}/records/{record}/terminate",
        )

    # ================= 环境 / 目标 / 集群 =================
    async def list_envs(
        self, project: Optional[str] = None, page: int = 0, page_size: int = 20
    ) -> dict[str, Any]:
        """GET /envs"""
        return await self.request(
            "GET",
            "/envs",
            params={"project": project, "page": page, "pageSize": page_size},
        )

    async def list_targets(
        self, project: Optional[str] = None, page: int = 0, page_size: int = 20
    ) -> dict[str, Any]:
        """GET /targets"""
        return await self.request(
            "GET",
            "/targets",
            params={"project": project, "page": page, "pageSize": page_size},
        )

    async def list_clusters(
        self, query: Optional[str] = None, page: int = 0, page_size: int = 20
    ) -> dict[str, Any]:
        """GET /clusters"""
        return await self.request(
            "GET",
            "/clusters",
            params={"query": query, "page": page, "pageSize": page_size},
        )

    async def get_cluster(self, cluster_name: str) -> dict[str, Any]:
        """GET /clusters/{clusterName}"""
        return await self.request("GET", f"/clusters/{cluster_name}")

    # ================= 项目 =================
    async def list_projects(
        self, page: int = 0, page_size: int = 20
    ) -> dict[str, Any]:
        """GET /projects"""
        return await self.request(
            "GET", "/projects", params={"page": page, "pageSize": page_size}
        )

    async def list_project_targets(self, project_name: str) -> dict[str, Any]:
        """GET /projects/{projectName}/targets"""
        return await self.request("GET", f"/projects/{project_name}/targets")

    async def list_project_users(self, project_name: str) -> dict[str, Any]:
        """GET /projects/{projectName}/users"""
        return await self.request("GET", f"/projects/{project_name}/users")

    # ================= 插件 =================
    async def list_addons(
        self, registry: Optional[str] = None, query: Optional[str] = None
    ) -> dict[str, Any]:
        """GET /addons"""
        return await self.request(
            "GET", "/addons", params={"registry": registry, "query": query}
        )

    async def get_addon(
        self,
        addon_name: str,
        version: Optional[str] = None,
        registry: Optional[str] = None,
    ) -> dict[str, Any]:
        """GET /addons/{addonName}"""
        return await self.request(
            "GET",
            f"/addons/{addon_name}",
            params={"version": version, "registry": registry},
        )

    async def get_addon_status(self, addon_name: str) -> dict[str, Any]:
        """GET /addons/{addonName}/status"""
        return await self.request("GET", f"/addons/{addon_name}/status")

    async def list_enabled_addons(self) -> dict[str, Any]:
        """GET /enabled_addon"""
        return await self.request("GET", "/enabled_addon")

    # ================= 定义 / VelaQL / 系统 =================
    async def list_definitions(
        self,
        def_type: str,
        query_all: bool = False,
    ) -> dict[str, Any]:
        """GET /definitions?type=（component|trait|workflowstep|policy）"""
        return await self.request(
            "GET",
            "/definitions",
            params={"type": def_type, "queryAll": str(query_all).lower()},
        )

    async def get_definition(
        self, definition_name: str, def_type: str
    ) -> dict[str, Any]:
        """GET /definitions/{definitionName}?type="""
        return await self.request(
            "GET", f"/definitions/{definition_name}", params={"type": def_type}
        )

    async def velaql_query(self, velaql: str) -> dict[str, Any]:
        """GET /query?velaql=（动态结构透传）"""
        return await self.request("GET", "/query", params={"velaql": velaql})

    async def get_system_info(self) -> dict[str, Any]:
        """GET /system_info"""
        return await self.request("GET", "/system_info")
