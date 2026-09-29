// Friendly names for the sites Planly knows. Any other site works too (read the same way).
const SITES = [
  ["iitm", "IITM BS", ["onlinedegree.iitm.ac.in", "iitm.ac.in"]],
  ["youtube", "YouTube", ["youtube.com", "youtu.be"]],
  ["striver", "Striver / takeUforward", ["takeuforward.org"]],
  ["coursera", "Coursera", ["coursera.org"]],
  ["udemy", "Udemy", ["udemy.com"]],
  ["leetcode", "LeetCode", ["leetcode.com"]],
  ["codeforces", "Codeforces", ["codeforces.com"]],
  ["codechef", "CodeChef", ["codechef.com"]],
  ["gfg", "GeeksforGeeks", ["geeksforgeeks.org"]],
  ["rankers", "Rankers Gurukul", ["rankersgurukul.com"]],
  ["parmar", "Parmar Academy", ["parmaracademy.in"]],
  ["testbook", "Testbook", ["testbook.com"]],
];

export function site(url) {
  const host = new URL(url).hostname.toLowerCase();
  for (const [name, label, domains] of SITES) {
    if (domains.some((d) => host === d || host.endsWith("." + d))) return { name, label, host };
  }
  const base = host.replace(/^www\./, "").split(".")[0] || "web";
  return { name: base, label: host, host };
}

// The permission Chrome grants when you switch Planly on: this one site, nothing else.
export function originPattern(url) {
  const u = new URL(url);
  return `${u.protocol}//${u.host}/*`;
}

export function isWebPage(url) {
  return /^https?:\/\//.test(url || "");
}
