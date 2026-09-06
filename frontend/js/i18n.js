const i18n = {
    _locale: localStorage.getItem('locale') || 'zh',
    _fallback: 'zh',

    _messages: {
        zh: {
            'app.title': '知识中心',
            'app.theme.toggle': '切换主题',
            'app.theme.dark': '深色模式',
            'app.theme.light': '浅色模式',
            'app.nav.kg': '知识图谱',
            'app.nav.rag': '智能检索',
            'app.nav.wiki': '知识Wiki',
            'app.nav.settings': '设置',
            'app.file.open': '打开文件夹',
            'app.file.new': '新建文件',
            'app.file.newFolder': '新建文件夹',
            'app.file.save': '保存',
            'app.file.convert': '格式转换',
            'kg.empty': '暂无图谱数据，请打开文件或上传文档构建知识图谱',
            'kg.build': '构建图谱',
            'kg.upload': '上传构建',
            'kg.clear': '清空图谱',
            'kg.export': '导出图谱',
            'kg.query.placeholder': '输入问题查询知识图谱…',
            'kg.reasoning.trace': '推理路径追踪',
            'kg.aggregation.hint': '聚合模式',
            'kg.aggregation.detail': '放大查看详情',
            'kg.legend.entity': '实体类型',
            'kg.legend.relation': '关系类型',
            'kg.chat.welcome': '你好！我是知识图谱问答助手。你可以基于知识图谱向我提问，我会通过实体链接和关系推理来回答你的问题。',
            'rag.search.placeholder': '输入问题进行智能检索…',
            'rag.search.mode': '搜索模式',
            'rag.mode.hybrid': '混合检索',
            'rag.mode.semantic': '语义检索',
            'rag.mode.keyword': '关键词检索',
            'rag.web.search': '联网搜索',
            'rag.web.search.on': '联网搜索已开启',
            'rag.web.search.off': '联网搜索已关闭',
            'rag.kb.select': '选择知识库',
            'rag.kb.all': '全部知识库',
            'rag.kb.empty': '暂无知识库，点击上方按钮创建',
            'rag.kb.create': '创建知识库',
            'rag.kb.upload': '上传文档',
            'rag.no.results': '暂无检索结果',
            'rag.hint': '输入问题后按回车或点击搜索按钮进行检索',
            'wiki.empty': '暂无Wiki页面',
            'wiki.create': '创建页面',
            'wiki.search': '搜索页面',
            'wiki.edit': '编辑',
            'wiki.save': '保存',
            'wiki.cancel': '取消',
            'wiki.history': '历史版本',
            'wiki.comments': '评论',
            'wiki.tags': '标签',
            'common.loading': '加载中…',
            'common.success': '操作成功',
            'common.error': '操作失败',
            'common.confirm': '确认',
            'common.cancel': '取消',
            'common.delete': '删除',
            'common.edit': '编辑',
            'common.save': '保存',
            'common.search': '搜索',
            'common.close': '关闭',
            'common.retry': '重试',
            'common.noData': '暂无数据',
        },
        en: {
            'app.title': 'Knowledge Hub',
            'app.theme.toggle': 'Toggle Theme',
            'app.theme.dark': 'Dark Mode',
            'app.theme.light': 'Light Mode',
            'app.nav.kg': 'Knowledge Graph',
            'app.nav.rag': 'Smart Search',
            'app.nav.wiki': 'Wiki',
            'app.nav.settings': 'Settings',
            'app.file.open': 'Open Folder',
            'app.file.new': 'New File',
            'app.file.newFolder': 'New Folder',
            'app.file.save': 'Save',
            'app.file.convert': 'Convert',
            'kg.empty': 'No graph data. Open a file or upload a document to build the knowledge graph.',
            'kg.build': 'Build Graph',
            'kg.upload': 'Upload & Build',
            'kg.clear': 'Clear Graph',
            'kg.export': 'Export Graph',
            'kg.query.placeholder': 'Ask a question about the knowledge graph…',
            'kg.reasoning.trace': 'Reasoning Path',
            'kg.aggregation.hint': 'Aggregation Mode',
            'kg.aggregation.detail': 'Zoom in for details',
            'kg.legend.entity': 'Entity Types',
            'kg.legend.relation': 'Relation Types',
            'kg.chat.welcome': 'Hello! I am the knowledge graph Q&A assistant. Ask me questions based on the knowledge graph, and I will answer through entity linking and relationship reasoning.',
            'rag.search.placeholder': 'Enter a question for smart search…',
            'rag.search.mode': 'Search Mode',
            'rag.mode.hybrid': 'Hybrid',
            'rag.mode.semantic': 'Semantic',
            'rag.mode.keyword': 'Keyword',
            'rag.web.search': 'Web Search',
            'rag.web.search.on': 'Web search enabled',
            'rag.web.search.off': 'Web search disabled',
            'rag.kb.select': 'Select Knowledge Base',
            'rag.kb.all': 'All Knowledge Bases',
            'rag.kb.empty': 'No knowledge bases yet. Click above to create one.',
            'rag.kb.create': 'Create KB',
            'rag.kb.upload': 'Upload Doc',
            'rag.no.results': 'No search results',
            'rag.hint': 'Enter a question and press Enter or click the search button',
            'wiki.empty': 'No Wiki pages',
            'wiki.create': 'Create Page',
            'wiki.search': 'Search Pages',
            'wiki.edit': 'Edit',
            'wiki.save': 'Save',
            'wiki.cancel': 'Cancel',
            'wiki.history': 'History',
            'wiki.comments': 'Comments',
            'wiki.tags': 'Tags',
            'common.loading': 'Loading…',
            'common.success': 'Success',
            'common.error': 'Error',
            'common.confirm': 'Confirm',
            'common.cancel': 'Cancel',
            'common.delete': 'Delete',
            'common.edit': 'Edit',
            'common.save': 'Save',
            'common.search': 'Search',
            'common.close': 'Close',
            'common.retry': 'Retry',
            'common.noData': 'No data',
        },
    },

    get locale() { return this._locale; },
    set locale(val) {
        this._locale = val;
        localStorage.setItem('locale', val);
        document.documentElement.setAttribute('data-locale', val);
    },

    t(key, params) {
        const messages = this._messages[this._locale] || this._messages[this._fallback];
        let text = messages[key];
        if (text === undefined) {
            const fallback = this._messages[this._fallback];
            text = fallback[key];
        }
        if (text === undefined) return key;
        if (params) {
            for (const [k, v] of Object.entries(params)) {
                text = text.replace(new RegExp(`\\{${k}\\}`, 'g'), v);
            }
        }
        return text;
    },

    getMessages() {
        return this._messages[this._locale] || this._messages[this._fallback];
    },

    registerMessages(locale, messages) {
        if (!this._messages[locale]) {
            this._messages[locale] = {};
        }
        Object.assign(this._messages[locale], messages);
    },
};

window.i18n = i18n;
window.t = i18n.t.bind(i18n);
