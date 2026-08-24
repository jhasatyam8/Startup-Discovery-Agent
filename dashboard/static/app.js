document.addEventListener("DOMContentLoaded", () => {
    let currentPage = 1;
    const itemsPerPage = 15;
    let currentSearch = "";
    let currentRoundFilter = "";
    let currentSort = "date";

    // Shark Tank state
    let stPage = 1;
    const stPerPage = 20;
    let stSeasonFilter = "";
    let stSharkFilter = "";
    let stDealFilter = "";

    let activeTab = "dashboard"; // "dashboard" | "sharktank"

    let roundsChart = null;
    let investorsChart = null;
    
    const runBtn = document.getElementById("run-pipeline-btn");
    const btnText = document.getElementById("btn-text");
    const searchInput = document.getElementById("search-input");
    const roundFilter = document.getElementById("round-filter");
    const sortSelect = document.getElementById("sort-select");
    const tableBody = document.getElementById("startups-table-body");
    const prevBtn = document.getElementById("prev-page-btn");
    const nextBtn = document.getElementById("next-page-btn");
    const paginationInfo = document.getElementById("pagination-info");
    
    fetchSummaryMetrics();
    fetchStartups();
    checkPipelineStatus();

    setInterval(checkPipelineStatus, 5000);

    runBtn.addEventListener("click", triggerPipelineRun);

    const runLeadsBtn = document.getElementById("btn-run-leads");
    const runReportBtn = document.getElementById("btn-run-report");
    const runSheetsBtn = document.getElementById("btn-run-sheets");

    if (runLeadsBtn) runLeadsBtn.addEventListener("click", () => triggerModularPipeline("/api/run-leads", runLeadsBtn, "text-run-leads", "👔 Find LinkedIn Leads"));
    if (runReportBtn) runReportBtn.addEventListener("click", () => triggerModularPipeline("/api/run-report", runReportBtn, "text-run-report", "📊 Generate Daily Report"));
    if (runSheetsBtn) runSheetsBtn.addEventListener("click", () => triggerModularPipeline("/api/run-sheets", runSheetsBtn, "text-run-sheets", "☁️ Sync to Google Sheets"));

    // Nav: Shark Tank tab
    document.getElementById("nav-sharktank").addEventListener("click", (e) => {
        e.preventDefault();
        activeTab = "sharktank";
        document.querySelectorAll(".nav-item").forEach(li => li.classList.remove("active"));
        e.currentTarget.closest(".nav-item").classList.add("active");
        document.getElementById("shark-tank-section").style.display = "block";
        document.getElementById("startups-section").style.display = "none";
        document.querySelectorAll(".charts-grid, .metrics-grid").forEach(el => {
            if (!el.id.startsWith("st-")) el.style.display = "none";
        });
        loadSharkTankSummary();
        loadSharkTankTable();
    });

    document.getElementById("nav-dashboard").addEventListener("click", (e) => {
        e.preventDefault();
        activeTab = "dashboard";
        document.querySelectorAll(".nav-item").forEach(li => li.classList.remove("active"));
        e.currentTarget.closest(".nav-item").classList.add("active");
        document.getElementById("shark-tank-section").style.display = "none";
        document.getElementById("startups-section").style.display = "block";
        document.querySelectorAll(".charts-grid, .metrics-grid").forEach(el => {
            el.style.display = "";
        });
    });

    document.getElementById("nav-rag").addEventListener("click", (e) => {
        e.preventDefault();
        activeTab = "rag";
        document.querySelectorAll(".nav-item").forEach(li => li.classList.remove("active"));
        e.currentTarget.closest(".nav-item").classList.add("active");
        document.getElementById("shark-tank-section").style.display = "none";
        document.getElementById("startups-section").style.display = "none";
        document.getElementById("rag-section").classList.remove("hidden");
        document.getElementById("rag-section").style.display = "block";
        document.querySelectorAll(".charts-grid, .metrics-grid").forEach(el => {
            el.style.display = "none";
        });
    });

    const ragAskBtn = document.getElementById("rag-ask-btn");
    const ragInput = document.getElementById("rag-input");
    const ragOutputBox = document.getElementById("rag-output-box");
    const ragOutputContent = document.getElementById("rag-output-content");

    if (ragAskBtn && ragInput) {
        const handleRagAsk = async () => {
            const query = ragInput.value.trim();
            if (!query) return;

            ragAskBtn.disabled = true;
            ragAskBtn.textContent = "Searching...";
            ragOutputBox.style.display = "block";
            ragOutputContent.innerHTML = "<p>⏳ <i>Searching startup database via RAG vector similarity...</i></p>";

            try {
                const res = await fetch("/api/rag/ask", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ query })
                });
                const data = await res.json();
                if (data.status === "success") {
                    // Simple Markdown-like formatting for output
                    let formatted = data.answer
                        .replace(/\n/g, "<br>")
                        .replace(/\*\*(.*?)\*\*/g, "<strong>$1</strong>")
                        .replace(/\*(.*?)\*/g, "<em>$1</em>");
                    ragOutputContent.innerHTML = formatted;
                } else {
                    ragOutputContent.innerHTML = `<p style="color: #ef4444;">⚠️ Error: ${data.detail || "RAG search failed."}</p>`;
                }
            } catch (err) {
                ragOutputContent.innerHTML = `<p style="color: #ef4444;">⚠️ Connection error: ${err.message}</p>`;
            } finally {
                ragAskBtn.disabled = false;
                ragAskBtn.textContent = "Ask AI";
            }
        };

        ragAskBtn.addEventListener("click", handleRagAsk);
        ragInput.addEventListener("keypress", (e) => {
            if (e.key === "Enter") handleRagAsk();
        });
    }

    searchInput.addEventListener("input", debounce(() => {
        currentSearch = searchInput.value;
        currentPage = 1;
        fetchStartups();
    }, 300));

    roundFilter.addEventListener("change", () => {
        currentRoundFilter = roundFilter.value;
        currentPage = 1;
        fetchStartups();
    });

    sortSelect.addEventListener("change", () => {
        currentSort = sortSelect.value;
        currentPage = 1;
        fetchStartups();
    });

    prevBtn.addEventListener("click", () => {
        if (currentPage > 1) { currentPage--; fetchStartups(); }
    });
    nextBtn.addEventListener("click", () => { currentPage++; fetchStartups(); });

    // Shark Tank filters
    document.getElementById("st-season-filter").addEventListener("change", (e) => {
        stSeasonFilter = e.target.value; stPage = 1; loadSharkTankTable();
    });
    document.getElementById("st-shark-filter").addEventListener("change", (e) => {
        stSharkFilter = e.target.value; stPage = 1; loadSharkTankTable();
    });
    document.getElementById("st-deal-filter").addEventListener("change", (e) => {
        stDealFilter = e.target.value; stPage = 1; loadSharkTankTable();
    });
    document.getElementById("st-prev-btn").addEventListener("click", () => {
        if (stPage > 1) { stPage--; loadSharkTankTable(); }
    });
    document.getElementById("st-next-btn").addEventListener("click", () => {
        stPage++; loadSharkTankTable();
    });

    function debounce(func, wait) {
        let timeout;
        return function executedFunction(...args) {
            const later = () => { clearTimeout(timeout); func(...args); };
            clearTimeout(timeout);
            timeout = setTimeout(later, wait);
        };
    }

    function formatUSD(amountNumeric) {
        if (!amountNumeric) return "N/A";
        if (amountNumeric >= 1.0e9) {
            return `$${(amountNumeric / 1.0e9).toFixed(1)}B`;
        }
        if (amountNumeric >= 1.0e6) {
            return `$${(amountNumeric / 1.0e6).toFixed(1)}M`;
        }
        if (amountNumeric >= 1.0e3) {
            return `$${(amountNumeric / 1.0e3).toFixed(0)}K`;
        }
        return `$${amountNumeric}`;
    }

    async function fetchSummaryMetrics() {
        try {
            const response = await fetch("/api/summary");
            if (!response.ok) throw new Error("Metrics fetch failed");
            const data = await response.json();
            
            document.getElementById("metric-today").textContent = data.new_today;
            document.getElementById("metric-total").textContent = data.total_startups;
            document.getElementById("metric-funding").textContent = formatUSD(data.total_funding_usd);
            document.getElementById("metric-videos").textContent = data.videos_scanned;
            document.getElementById("metric-videos-detail").textContent = 
                `${data.videos_processed} Processed · ${data.videos_ignored} Ignored · ${data.videos_failed} Errors`;
            
            renderRoundsChart(data.rounds_breakdown);
            renderInvestorsChart(data.active_investors);
        } catch (error) {
            console.error("Error fetching metrics:", error);
        }
    }

    async function fetchStartups() {
        try {
            tableBody.innerHTML = `<tr><td colspan="8" style="text-align: center; color: var(--text-secondary); padding: 2rem;">Loading startups database...</td></tr>`;
            
            let url = `/api/startups?page=${currentPage}&limit=${itemsPerPage}&sort_by=${currentSort}&order=desc`;
            if (currentSearch) url += `&search=${encodeURIComponent(currentSearch)}`;
            if (currentRoundFilter) url += `&round_filter=${encodeURIComponent(currentRoundFilter)}`;
            
            const response = await fetch(url);
            if (!response.ok) throw new Error("Startups database fetch failed");
            const res = await response.json();
            
            const total = res.total;
            const limit = res.limit;
            const startIdx = total === 0 ? 0 : (currentPage - 1) * limit + 1;
            const endIdx = Math.min(currentPage * limit, total);
            
            paginationInfo.textContent = `Showing ${startIdx} to ${endIdx} of ${total} entries`;
            
            prevBtn.disabled = currentPage === 1;
            nextBtn.disabled = currentPage >= res.total_pages;
            
            if (res.data.length === 0) {
                tableBody.innerHTML = `<tr><td colspan="8" style="text-align: center; color: var(--text-secondary); padding: 2rem;">No startups found. Run a discovery task!</td></tr>`;
                return;
            }
            
            tableBody.innerHTML = "";
            res.data.forEach(s => {
                const tr = document.createElement("tr");
                
                const nameCell = document.createElement("td");
                nameCell.className = "startup-name-cell";
                nameCell.innerHTML = s.website && s.website.startsWith("http") 
                    ? `<a href="${s.website}" target="_blank" class="website-link">${s.name} ↗</a>`
                    : s.name;
                tr.appendChild(nameCell);
                
                const roundCell = document.createElement("td");
                roundCell.innerHTML = s.funding_round 
                    ? `<span class="round-badge">${s.funding_round}</span>`
                    : '<span style="color: var(--text-muted);">N/A</span>';
                tr.appendChild(roundCell);
                
                const amountCell = document.createElement("td");
                amountCell.className = "amount-text";
                amountCell.textContent = s.funding_amount || "N/A";
                tr.appendChild(amountCell);
                
                const investorsCell = document.createElement("td");
                if (s.investors && s.investors.length > 0) {
                    s.investors.slice(0, 3).forEach(inv => {
                        const span = document.createElement("span");
                        span.className = "investor-tag";
                        span.textContent = inv;
                        investorsCell.appendChild(span);
                    });
                    if (s.investors.length > 3) {
                        const span = document.createElement("span");
                        span.className = "investor-tag";
                        span.style.color = "var(--accent-primary)";
                        span.textContent = `+${s.investors.length - 3} more`;
                        investorsCell.appendChild(span);
                    }
                } else {
                    investorsCell.innerHTML = '<span style="color: var(--text-muted);">Undisclosed</span>';
                }
                tr.appendChild(investorsCell);
                
                const industryCell = document.createElement("td");
                industryCell.textContent = s.industry || "General";
                tr.appendChild(industryCell);
                
                const hqCell = document.createElement("td");
                hqCell.textContent = s.hq || "-";
                tr.appendChild(hqCell);
                
                const confidenceCell = document.createElement("td");
                const confVal = s.confidence_score || 0.0;
                const confPercent = Math.round(confVal * 100);
                
                let confClass = "low";
                if (confVal >= 0.75) confClass = "high";
                else if (confVal >= 0.45) confClass = "mid";
                
                confidenceCell.innerHTML = `
                    <div class="confidence-wrapper">
                        <div class="confidence-bar-bg">
                            <div class="confidence-bar-fill ${confClass}" style="width: ${confPercent}%"></div>
                        </div>
                        <span class="confidence-val" style="color: ${confVal >= 0.75 ? 'var(--success)' : confVal >= 0.45 ? 'var(--warning)' : 'var(--danger)'}">${confPercent}%</span>
                    </div>
                `;
                tr.appendChild(confidenceCell);
                
                const verCell = document.createElement("td");
                if (s.verification_sources && s.verification_sources.length > 0) {
                    const firstSource = s.verification_sources[0];
                    verCell.innerHTML = `
                        <a href="${firstSource}" target="_blank" class="verification-badge" style="text-decoration: none;">
                            Verified ✓ (${s.verification_sources.length})
                        </a>
                    `;
                } else {
                    verCell.innerHTML = `<span class="verification-badge unverified">Unverified</span>`;
                }
                tr.appendChild(verCell);
                
                const sourceCell = document.createElement("td");
                const src = s.source || "youtube";
                let srcLabel = "";
                if (src === "inc42") {
                    srcLabel = `<span style="background:rgba(234,88,12,0.2);color:#fb923c;padding:2px 8px;border-radius:20px;font-size:0.75rem;">Inc42</span>`;
                } else if (src === "vision") {
                    srcLabel = `<span style="background:rgba(139,92,246,0.2);color:#a78bfa;padding:2px 8px;border-radius:20px;font-size:0.75rem;">👁️ Vision</span>`;
                } else {
                    srcLabel = `<a href="${s.source_video_url}" target="_blank" class="video-link-icon">
                        <svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor">
                            <path d="M19 19H5V5h7V3H5c-1.11 0-2 .9-2 2v14c0 1.1.89 2 2 2h14c1.1 0 2-.9 2-2v-7h-2v7zM14 3v2h3.59l-9.83 9.83 1.41 1.41L19 6.41V10h2V3h-7z"/>
                        </svg>
                    </a>`;
                }
                sourceCell.innerHTML = srcLabel;
                tr.appendChild(sourceCell);

                tableBody.appendChild(tr);
            });
        } catch (error) {
            console.error("Error fetching startups list:", error);
        }
    }

    // ===================== SHARK TANK FUNCTIONS =====================

    async function loadSharkTankSummary() {
        try {
            const res = await fetch("/api/shark-tank/summary");
            const d = await res.json();
            document.getElementById("st-total").textContent = d.total_startups;
            document.getElementById("st-deals").textContent = d.deals_made;
            document.getElementById("st-nodeal").textContent = d.no_deal;
            document.getElementById("st-deal-rate").textContent = `${d.deal_rate_pct}% success rate`;
            if (d.top_sharks && d.top_sharks.length > 0) {
                const top = d.top_sharks[0];
                document.getElementById("st-top-shark").textContent = `${top.name} (${top.deals} deals)`;
            }
        } catch(e) { console.error("ST summary error:", e); }
    }

    async function loadSharkTankTable() {
        const tbody = document.getElementById("st-table-body");
        tbody.innerHTML = `<tr><td colspan="8" style="text-align:center;color:var(--text-secondary);padding:2rem;">Loading...</td></tr>`;
        try {
            let url = `/api/shark-tank?page=${stPage}&limit=${stPerPage}`;
            if (stSeasonFilter) url += `&season=${stSeasonFilter}`;
            if (stSharkFilter) url += `&shark=${encodeURIComponent(stSharkFilter)}`;
            if (stDealFilter !== "") url += `&deal_made=${stDealFilter}`;

            const res = await fetch(url);
            const d = await res.json();

            const total = d.total;
            const offset = (stPage - 1) * stPerPage;
            document.getElementById("st-pagination-info").textContent =
                `Showing ${total === 0 ? 0 : offset + 1}–${Math.min(offset + stPerPage, total)} of ${total}`;
            document.getElementById("st-prev-btn").disabled = stPage <= 1;
            document.getElementById("st-next-btn").disabled = stPage >= d.total_pages;

            if (d.data.length === 0) {
                tbody.innerHTML = `<tr><td colspan="8" style="text-align:center;color:var(--text-secondary);padding:2rem;">No results found.</td></tr>`;
                return;
            }

            tbody.innerHTML = "";
            const SHARK_COLORS = {
                "Aman Gupta":    "#06b6d4",
                "Namita Thapar": "#ec4899",
                "Peyush Bansal": "#8b5cf6",
                "Anupam Mittal": "#f59e0b",
                "Vineeta Singh": "#10b981",
                "Ashneer Grover":"#ef4444",
                "Ghazal Alagh":  "#a78bfa",
                "Amit Jain":     "#34d399",
                "Radhika Gupta": "#fb923c",
                "Kunal Bahl":    "#60a5fa",
            };

            d.data.forEach(s => {
                const tr = document.createElement("tr");

                tr.innerHTML = `
                    <td class="startup-name-cell">${s.website ? `<a href="${s.website}" target="_blank" class="website-link">${s.name} ↗</a>` : s.name}</td>
                    <td><span class="round-badge">S${s.season || '?'}</span></td>
                    <td>${s.sector || 'Unknown'}</td>
                    <td class="amount-text">${s.ask_amount || 'N/A'}</td>
                    <td class="amount-text">${s.deal_made ? (s.deal_amount || 'Deal') : '<span style="color:var(--text-muted)">—</span>'}</td>
                    <td>${s.equity_pct != null ? s.equity_pct + '%' : '—'}</td>
                    <td>${(s.sharks || []).map(sh => `<span class="investor-tag" style="border-color:${SHARK_COLORS[sh] || '#9ca3af'}40;color:${SHARK_COLORS[sh] || '#9ca3af'}">${sh.split(' ')[0]}</span>`).join('') || '<span style="color:var(--text-muted)">—</span>'}</td>
                    <td>${s.deal_made ? '<span style="color:#10b981;font-weight:600;">✅ Deal</span>' : '<span style="color:#ef4444;">❌ No Deal</span>'}</td>
                `;
                tbody.appendChild(tr);
            });
        } catch(e) {
            console.error("ST table error:", e);
            tbody.innerHTML = `<tr><td colspan="8" style="text-align:center;color:var(--danger);padding:2rem;">Error loading data.</td></tr>`;
        }
    }

    async function triggerPipelineRun() {
        try {
            runBtn.disabled = true;
            btnText.textContent = "Pipeline Working...";
            runBtn.innerHTML = `<span class="spinner"></span> Working...`;
            
            const response = await fetch("/api/run-pipeline", { method: "POST" });
            const data = await response.json();
            
            console.log("Triggered pipeline:", data.message);
            checkPipelineStatus();
        } catch (error) {
            console.error("Error triggering run:", error);
            runBtn.disabled = false;
            runBtn.innerHTML = `Run Discovery Now`;
        }
    }

    async function triggerModularPipeline(endpoint, btnEl, textId, originalText) {
        try {
            btnEl.disabled = true;
            document.getElementById(textId).innerHTML = `<span class="spinner" style="border-width: 2px; width: 12px; height: 12px; margin-right: 5px; display: inline-block;"></span> Working...`;
            
            const response = await fetch(endpoint, { method: "POST" });
            const data = await response.json();
            
            console.log(`Triggered ${endpoint}:`, data.message);
            alert(data.message);
        } catch (error) {
            console.error(`Error triggering ${endpoint}:`, error);
            alert(`Error triggering pipeline step. Check console.`);
        } finally {
            setTimeout(() => {
                btnEl.disabled = false;
                document.getElementById(textId).innerHTML = originalText;
            }, 3000);
        }
    }

    async function checkPipelineStatus() {
        try {
            const response = await fetch("/api/pipeline-status");
            const data = await response.json();
            
            if (data.status === "running") {
                runBtn.disabled = true;
                runBtn.innerHTML = `<span class="spinner"></span> Discovery Running...`;
            } else {
                runBtn.disabled = false;
                runBtn.innerHTML = `
                    <svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor" style="margin-right:0.25rem;">
                        <path d="M12 4V1L8 5l4 4V6c3.31 0 6 2.69 6 6 0 1.01-.25 1.97-.7 2.8l1.46 1.46C19.54 15.03 20 13.57 20 12c0-4.42-3.58-8-8-8zm-6 8c0-1.01.25-1.97.7-2.8L5.24 7.74C4.46 8.97 4 10.43 4 12c0 4.42 3.58 8 8 8v3l4-4-4-4v3c-3.31 0-6-2.69-6-6z"/>
                    </svg>
                    Run Discovery Now
                `;
                if (btnText && btnText.textContent === "Pipeline Working...") {
                    fetchSummaryMetrics();
                    fetchStartups();
                }
            }
        } catch (error) {
            console.error("Error checking pipeline status:", error);
        }
    }

    function renderRoundsChart(roundsBreakdown) {
        const ctx = document.getElementById("roundsChart").getContext("2d");
        
        const labels = Object.keys(roundsBreakdown);
        const data = Object.values(roundsBreakdown);
        
        if (roundsChart) {
            roundsChart.destroy();
        }
        
        if (labels.length === 0) {
            labels.push("No data");
            data.push(1);
        }

        roundsChart = new Chart(ctx, {
            type: "doughnut",
            data: {
                labels: labels,
                datasets: [{
                    data: data,
                    backgroundColor: [
                        "#8b5cf6",
                        "#06b6d4",
                        "#10b981",
                        "#f59e0b",
                        "#ec4899",
                        "#3b82f6",
                        "#6b7280"
                    ],
                    borderWidth: 1,
                    borderColor: "rgba(255, 255, 255, 0.08)"
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: {
                        position: "right",
                        labels: {
                            color: "#9ca3af",
                            font: { family: "Outfit", size: 12 }
                        }
                    }
                }
            }
        });
    }

    function renderInvestorsChart(activeInvestors) {
        const ctx = document.getElementById("investorsChart").getContext("2d");
        
        const labels = activeInvestors.map(i => i.name);
        const data = activeInvestors.map(i => i.count);
        
        if (investorsChart) {
            investorsChart.destroy();
        }
        
        investorsChart = new Chart(ctx, {
            type: "bar",
            data: {
                labels: labels,
                datasets: [{
                    label: "Startups Funded",
                    data: data,
                    backgroundColor: "rgba(6, 182, 212, 0.75)",
                    hoverBackgroundColor: "rgba(6, 182, 212, 1)",
                    borderRadius: 6,
                    borderWidth: 0
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: { display: false }
                },
                scales: {
                    x: {
                        grid: { display: false },
                        ticks: {
                            color: "#9ca3af",
                            font: { family: "Outfit", size: 10 }
                        }
                    },
                    y: {
                        grid: { color: "rgba(255, 255, 255, 0.04)" },
                        ticks: {
                            color: "#9ca3af",
                            precision: 0,
                            font: { family: "Outfit" }
                        }
                    }
                }
            }
        });
    }
});
