// --- BEGIN COPYRIGHT BLOCK ---
// This program is free software; you can redistribute it and/or modify
// it under the terms of the GNU General Public License as published by
// the Free Software Foundation; version 2 of the License.
//
// This program is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
// GNU General Public License for more details.
//
// You should have received a copy of the GNU General Public License along
// with this program; if not, write to the Free Software Foundation, Inc.,
// 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA.
//
// (C) 2012 Red Hat, Inc.
// All rights reserved.
// --- END COPYRIGHT BLOCK ---

package com.netscape.cmstools.ca;

import java.io.BufferedReader;
import java.io.InputStreamReader;

import org.apache.commons.cli.CommandLine;
import org.apache.commons.cli.Option;
import org.dogtagpki.cli.CommandCLI;
import org.mozilla.jss.netscape.security.x509.RevocationReason;

import com.netscape.certsrv.ca.CACertClient;
import com.netscape.certsrv.cert.CertData;
import com.netscape.certsrv.cert.CertRequestInfo;
import com.netscape.certsrv.cert.CertRevokeRequest;
import com.netscape.certsrv.dbs.certdb.CertId;
import com.netscape.certsrv.request.RequestStatus;
import com.netscape.cmstools.cli.MainCLI;

/**
 * @author Endi S. Dewata
 */
public class CACertHoldCLI extends CommandCLI {

    public static org.slf4j.Logger logger = org.slf4j.LoggerFactory.getLogger(CACertHoldCLI.class);

    public CACertCLI certCLI;

    public CACertHoldCLI(CACertCLI certCLI) {
        super("hold", "Place certificate on-hold", certCLI);
        this.certCLI = certCLI;
    }

    @Override
    public void printHelp() {
        formatter.printHelp(getFullName() + " <Serial Number> [OPTIONS...]", options);
    }

    @Override
    public void createOptions() {
        Option option = new Option(null, "comments", true, "Comments");
        option.setArgName("comments");
        options.addOption(option);

        options.addOption(null, "force", false, "Force");
    }

    @Override
    public void execute(CommandLine cmd) throws Exception {

        String[] cmdArgs = cmd.getArgs();

        if (cmdArgs.length != 1) {
            throw new Exception("Missing Serial Number.");
        }

        CertId certID = new CertId(cmdArgs[0]);

        MainCLI mainCLI = (MainCLI) getRoot();
        mainCLI.init();

        CACertClient certClient = certCLI.getCertClient();
        CertData certData = certClient.reviewCert(certID);

        if (!cmd.hasOption("force")) {

            System.out.println("Placing certificate on-hold:");

            CACertCLI.printCertData(certData, false, false);
            logger.info("Nonce: " + certData.getNonce());

            System.out.print("Are you sure (Y/N)? ");
            System.out.flush();

            BufferedReader reader = new BufferedReader(new InputStreamReader(System.in));
            String line = reader.readLine();
            if (!line.equalsIgnoreCase("Y")) {
                return;
            }
        }

        CertRevokeRequest request = new CertRevokeRequest();
        request.setReason(RevocationReason.CERTIFICATE_HOLD.getLabel());
        request.setComments(cmd.getOptionValue("comments"));
        request.setNonce(certData.getNonce());

        CertRequestInfo certRequestInfo = certClient.revokeCert(certID, request);

        if (logger.isInfoEnabled()) {
            CACertRequestCLI.printCertRequestInfo(certRequestInfo);
        }

        if (certRequestInfo.getRequestStatus() == RequestStatus.COMPLETE) {
            if (certRequestInfo.getOperationResult().equals(CertRequestInfo.RES_ERROR)) {
                String error = certRequestInfo.getErrorMessage();
                if (error != null) {
                    System.out.println(error);
                }
                MainCLI.printMessage("Could not place certificate \"" + certID.toHexString() + "\" on-hold");
            } else {
                MainCLI.printMessage("Placed certificate \"" + certID.toHexString() + "\" on-hold");
                certData = certClient.getCert(certID);
                CACertCLI.printCertData(certData, false, false);
            }
        } else {
            MainCLI.printMessage("Request \"" + certRequestInfo.getRequestID().toHexString() + "\": "
                    + certRequestInfo.getRequestStatus());
        }
    }
}
