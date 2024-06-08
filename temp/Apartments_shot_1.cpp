#include <iostream>
#include <algorithm>
#include <vector>

using namespace std;

int main() {
    int n, m, k;
    cin >> n >> m >> k;

    vector<int> applicants(n);
    vector<int> apartments(m);

    for (int i = 0; i < n; i++) {
        cin >> applicants[i];
    }

    for (int i = 0; i < m; i++) {
        cin >> apartments[i];
    }

    sort(applicants.begin(), applicants.end());
    sort(apartments.begin(), apartments.end());

    int i = 0, j = 0, count = 0;

    while (i < n && j < m) {
        if (abs(applicants[i] - apartments[j]) <= k) {
            count++;
            i++;
            j++;
        } else if (applicants[i] < apartments[j]) {
            i++;
        } else {
            j++;
        }
    }

    cout << count << endl;

    return 0;
}